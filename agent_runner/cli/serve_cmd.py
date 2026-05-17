"""serve subcommand — long-running supervisor loop.

THIN dispatcher: orchestrates the supervisor loop, delegates all helpers to
``agent_runner.round_log`` (round-log file ops) and ``agent_runner.api``
(sentinel + round counter).

Trap signals, write/cleanup PID files, run `round` subprocess in a loop.
All real work delegated to `agent-runner round` (fresh import per round).
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess  # noqa: TID251
import sys
import time
from pathlib import Path

from agent_runner._substrate import compute_git_head, compute_paths_hash
from agent_runner._throttle import _check_throttle_state
from agent_runner.api import (
    check_self_terminated_sentinel,
    emit_fresh_eyes_round_triggered,
    emit_max_rounds_reached,
    emit_rate_limit_stop,
    emit_round_substrate_after,
    emit_round_substrate_before,
    emit_stop_file_detected,
)
from agent_runner.cli.common import cfg_from_args
from agent_runner.hooks import run_serve_startup_hooks
from agent_runner.lifecycle import PIDFile, send_signal_to_pid
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


def _build_serve_parser() -> argparse.ArgumentParser:
    """Return a standalone argument parser for the serve subcommand.

    Used for unit testing; production wiring goes through add_parser().
    """
    p = argparse.ArgumentParser(prog="agent-runner serve")
    p.add_argument("--config", type=Path, default=Path("./agent-runner.toml"))
    p.add_argument("--once", action="store_true", help="Run a single round then exit (debug)")
    _add_max_rounds_arg(p)
    return p


def add_parser(sub, parent) -> None:
    p = sub.add_parser("serve", parents=[parent], help="Long-running supervisor loop")
    p.add_argument("--once", action="store_true", help="Run a single round then exit (debug)")
    _add_max_rounds_arg(p)
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    cfg = cfg_from_args(args)
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    if not run_serve_startup_hooks(cfg, log_dir):
        return 1

    pid_file = PIDFile(log_dir / "serve.pid")
    stop = {"requested": False}
    round_pid_file = PIDFile(log_dir / "round.pid")

    def graceful(_sig, _frame):
        stop["requested"] = True

    def cancel(_sig, _frame):
        stop["requested"] = True
        rp = round_pid_file.read()
        if rp is not None:
            send_signal_to_pid(-rp, signal.SIGINT)

    # Arm signals before any pre-loop cleanup — a SIGTERM arriving during
    # sentinel removal or log pruning will set stop["requested"] and the
    # loop will not start rather than killing with the default handler.
    signal.signal(signal.SIGTERM, graceful)
    signal.signal(signal.SIGINT, graceful)
    signal.signal(signal.SIGUSR1, cancel)

    # Pre-loop cleanup: remove stale sentinel, prune old round logs.
    (log_dir / ".agent-done").unlink(missing_ok=True)
    prune_old_round_logs(log_dir, cfg.runtime.round_log_retention)

    round_env = {**os.environ, "AGENT_RUNNER_LOG_DIR": str(log_dir)}

    effective_max_rounds = _resolve_max_rounds(
        cli_value=args.max_rounds, config_value=cfg.runtime.max_rounds
    )
    stop_file = cfg.runtime.stop_file  # cache: same pattern as effective_max_rounds
    work_dir = cfg.runtime.work_dir
    rounds_completed = 0

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
            if args.once or stop["requested"]:
                break
            delay = (
                cfg.runtime.restart_delay_s
                if r.returncode == 0
                else cfg.runtime.restart_delay_s * 2
            )
            time.sleep(delay)
    finally:
        pid_file.unlink()
    return 0
