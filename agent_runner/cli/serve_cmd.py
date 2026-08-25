"""serve subcommand — long-running supervisor loop.

THIN dispatcher: orchestrates the supervisor loop, delegates all helpers to
``agent_runner.round_log`` (round-log file ops) and ``agent_runner.api``
(sentinel + round counter).

Trap signals, write/cleanup the serve PID file, run `round` subprocess in a loop.
All real work delegated to `agent-runner round` (fresh import per round).
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: TID251
import sys
import time
from pathlib import Path

from agent_runner import schedule
from agent_runner._substrate import compute_git_head, compute_paths_hash
from agent_runner._throttle import _check_throttle_state
from agent_runner._throttle import reset_counters as _reset_counters
from agent_runner.api import (
    check_self_terminated_sentinel,
    emit_config_broken,
    emit_crash_loop,
    emit_fresh_eyes_round_triggered,
    emit_max_rounds_reached,
    emit_rate_limit_stop,
    emit_round_logs_prune_deferred,
    emit_round_substrate_after,
    emit_round_substrate_before,
    emit_schedule_paused,
    emit_schedule_resumed,
    emit_stop_file_detected,
    post_round_decision,
)
from agent_runner.cli.common import cfg_from_args
from agent_runner.hooks import run_serve_startup_hooks
from agent_runner.lifecycle import PIDFile
from agent_runner.round_log import (
    ROUND_CURRENT_LINK,
    atomic_relink,
    next_round_num,
    prune_old_round_logs,
)
from agent_runner.runner import _apply_back_off


def _resolve_max_rounds(*, cli_value: int | None, config_value: int | None) -> int | None:
    """Resolve effective max_rounds: CLI flag overrides [runtime] config value.

    Returns None if neither is set (unbounded). Raises ValueError if the CLI
    value is <= 0 (argparse type=int admits zero/negative; config_value is
    already validated by _require_positive_int at load_config time).
    """
    effective = cli_value if cli_value is not None else config_value
    if effective is not None and effective < 1:
        raise ValueError(f"--max-rounds must be positive integer, got {effective}")
    return effective


def _maybe_pause_for_schedule(
    cfg,
    log_dir,
    stop,
    *,
    now_fn=schedule.now_in_zone,
    sleep_fn=time.sleep,
    chunk_s: int = 30,
) -> bool:
    """If the current time is outside the run schedule, pause until it opens.

    Returns True if a pause was entered (caller should ``continue`` so the
    top-of-loop guards re-run), False if runnable now. During a pause, sleeps
    in <= chunk_s slices so SIGTERM / SIGINT (which set stop["requested"]) lands
    within one slice.

    We do NOT check the self-terminate sentinel here: no round runs during a
    pause, so no new sentinel can appear, and any pre-existing one already broke
    the loop at its top before this gate. schedule_resumed is emitted ONLY when
    the window actually opens — an interrupted pause (stop) is a termination, not
    a resume, and correctly leaves schedule_paused as the newest event."""
    sched = cfg.schedule
    if not sched.enabled:
        return False
    decision = schedule.evaluate(
        run_windows=sched.run_windows,
        pause_windows=sched.pause_windows,
        now_local=now_fn(sched.timezone),
    )
    if not decision.paused:
        return False

    started = time.monotonic()
    emit_schedule_paused(
        log_dir,
        active_window=decision.active_window or "",
        resume_at=decision.resume_at.isoformat() if decision.resume_at else "",
        timezone=sched.timezone or "local",
    )
    stop_file = cfg.runtime.stop_file
    window_opened = False
    while not stop["requested"]:
        if stop_file is not None and stop_file.exists():
            # Operator stop_file dropped mid-pause: break without emitting
            # schedule_resumed (the window did not open). The serve loop's
            # top-of-loop stop_file check then emits stop_file_detected and exits.
            break
        # #3: should_run (not evaluate) avoids the 8-day next_resume_at scan on
        # every poll; should_run is the negation of the pre-loop paused decision.
        if schedule.should_run(
            now_fn(sched.timezone),
            run_windows=sched.run_windows,
            pause_windows=sched.pause_windows,
        ):
            window_opened = True
            break
        sleep_fn(chunk_s)
    if window_opened:
        emit_schedule_resumed(log_dir, paused_for_s=int(time.monotonic() - started))
    return True


def _prune_serve_round_logs(log_dir: Path, retention: int) -> None:
    """Prune the serve-level ``round-<N>.log`` family; report a deferred prune.

    A prune that would delete more files than it keeps is deferred wholesale
    (nothing is deleted) and surfaced as ``round_logs_prune_deferred`` — the
    same contract ``rounds/`` gets at round start, since one knob governs both.
    """
    outcome = prune_old_round_logs(log_dir, retention)
    if outcome.deferred:
        emit_round_logs_prune_deferred(
            log_dir,
            directory=str(log_dir),
            existing=outcome.existing,
            keep=retention,
            would_delete=outcome.deferred,
        )


def _is_fresh_eyes_round(*, round_num: int, every_n: int | None) -> bool:
    """True if this round should signal fresh-eyes mode to the agent subprocess.

    round_num <= 0 guard: very first round is never fresh-eyes (need warm
    baseline first). every_n=None → feature disabled.
    """
    if every_n is None or round_num <= 0:
        return False
    return round_num % every_n == 0


def _add_max_rounds_arg(parser) -> None:
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N round completions (overrides [runtime] max_rounds in config)",
    )


def add_parser(sub, parent) -> None:
    p = sub.add_parser("serve", parents=[parent], help="Long-running supervisor loop")
    p.add_argument("--once", action="store_true", help="Run a single round then exit (debug)")
    _add_max_rounds_arg(p)
    p.add_argument(
        "--ignore-schedule",
        action="store_true",
        help="Run rounds regardless of [schedule] pause/run windows (testing / catch-up)",
    )
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    cfg = cfg_from_args(args)
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    if not run_serve_startup_hooks(cfg, log_dir):
        return 1

    pid_file = PIDFile(log_dir / "serve.pid")
    stop = {"requested": False}

    def graceful(_sig, _frame):
        stop["requested"] = True

    # Arm signals before any pre-loop cleanup — a SIGTERM arriving during
    # sentinel removal or log pruning will set stop["requested"] and the
    # loop will not start rather than killing with the default handler.
    signal.signal(signal.SIGTERM, graceful)
    signal.signal(signal.SIGINT, graceful)

    # Pre-loop cleanup: remove stale sentinel, prune old round logs.
    (log_dir / ".agent-done").unlink(missing_ok=True)
    _prune_serve_round_logs(log_dir, cfg.runtime.round_log_retention)

    round_env = {**os.environ, "AGENT_RUNNER_LOG_DIR": str(log_dir)}

    effective_max_rounds = _resolve_max_rounds(
        cli_value=args.max_rounds, config_value=cfg.runtime.max_rounds
    )
    stop_file = cfg.runtime.stop_file  # cache: same pattern as effective_max_rounds
    work_dir = cfg.runtime.work_dir
    rounds_completed = 0
    consecutive_crashes = 0  # b12: consecutive UNKNOWN short crashes (crash-loop breaker)

    try:
        pid_file.write(os.getpid())
        while not stop["requested"]:
            if check_self_terminated_sentinel(log_dir):
                break
            throttle = _check_throttle_state(log_dir)
            if throttle is not None:
                action = cfg.runtime.transient_error_action
                if action == "back_off":
                    _apply_back_off(log_dir, throttle)
                    # Fall through to normal launch
                elif action == "skip":
                    pass  # Proceed to normal launch
                elif action == "stop":
                    emit_rate_limit_stop(log_dir)
                    break
            else:
                # No active throttle this round — supervisor counters can reset.
                # Next failure (if any) restarts the exp backoff curve from 1×.
                _reset_counters()
            if stop_file is not None and stop_file.exists():
                try:
                    content = stop_file.read_text(encoding="utf-8", errors="replace")[:200]
                except OSError:
                    content = ""
                emit_stop_file_detected(
                    log_dir,
                    stop_file=stop_file,
                    content=content,
                    rounds_completed=rounds_completed,
                )
                break
            if effective_max_rounds is not None and rounds_completed >= effective_max_rounds:
                emit_max_rounds_reached(
                    log_dir,
                    rounds_completed=rounds_completed,
                    max_rounds=effective_max_rounds,
                )
                break
            if not args.ignore_schedule and _maybe_pause_for_schedule(cfg, log_dir, stop):
                continue
            round_num = next_round_num(log_dir)
            git_head_before = compute_git_head(work_dir)
            paths_hash_before = compute_paths_hash(
                work_dir, cfg.runtime.substrate_fingerprint_paths
            )
            emit_round_substrate_before(
                log_dir,
                round_num=round_num,
                git_head=git_head_before,
                paths_hash=paths_hash_before,
            )
            fresh_eyes = _is_fresh_eyes_round(
                round_num=round_num, every_n=cfg.runtime.fresh_eyes_every_n
            )
            round_env["AGENT_RUNNER_FRESH_EYES"] = "1" if fresh_eyes else "0"
            if fresh_eyes:
                emit_fresh_eyes_round_triggered(
                    log_dir,
                    round_num=round_num,
                    every_n=cfg.runtime.fresh_eyes_every_n,
                )
            round_log_path = log_dir / f"round-{round_num}.log"
            round_started = time.monotonic()
            with round_log_path.open("w") as f:
                r = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "agent_runner.cli",
                        "--config",
                        str(args.config),
                        "round",
                    ],
                    env=round_env,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                )
            round_duration_s = time.monotonic() - round_started
            atomic_relink(log_dir / ROUND_CURRENT_LINK, round_log_path)
            git_head_after = compute_git_head(work_dir)
            paths_hash_after = compute_paths_hash(work_dir, cfg.runtime.substrate_fingerprint_paths)
            emit_round_substrate_after(
                log_dir,
                round_num=round_num,
                git_head=git_head_after,
                paths_hash=paths_hash_after,
            )
            rounds_completed += 1
            # Restart policy (config_broken / crash_loop / continue) lives in the
            # tested api.post_round_decision helper so this loop stays thin. Those
            # strings are that enum, not events.py kinds — do not normalize them.
            action, delay, consecutive_crashes = post_round_decision(
                returncode=r.returncode,
                duration_s=round_duration_s,
                throttle_active=_check_throttle_state(log_dir) is not None,
                consecutive=consecutive_crashes,
                restart_delay_s=cfg.runtime.restart_delay_s,
            )
            if action == "config_broken":
                emit_config_broken(log_dir, reason="startup battery permanent failure")
                break
            if action == "crash_loop":
                emit_crash_loop(
                    log_dir,
                    consecutive=consecutive_crashes,
                    exit_code=r.returncode,
                    log_path=round_log_path,
                )
                break
            if args.once or stop["requested"]:
                break
            time.sleep(delay)
    finally:
        pid_file.unlink()
    return 0
