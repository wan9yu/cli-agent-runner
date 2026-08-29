"""serve subcommand — long-running supervisor loop.

THIN dispatcher: orchestrates the supervisor loop, delegates all helpers to
``agent_runner.round_log`` (round-log file ops) and ``agent_runner.api``
(sentinel + round counter).

Trap signals, write/cleanup the serve PID file, run `round` subprocess in a loop.
All real work delegated to `agent-runner round` (fresh import per round).
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess  # noqa: TID251
import sys
from pathlib import Path
from typing import Literal

from agent_runner import phase_select, schedule
from agent_runner._substrate import compute_git_head, compute_paths_hash
from agent_runner._throttle import (
    _active_throttles,
    _apply_back_off,
    _check_throttle_state,
    _interruptible_sleep,
    pending_recovered,
)
from agent_runner._throttle import reset_counters as _reset_counters
from agent_runner.api import (
    CRASH_LOOP_EXIT,
    PERMANENT_CONFIG_EXIT,
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
    emit_schedule_phase_skipped,
    emit_schedule_resumed,
    emit_stop_file_detected,
    emit_transient_error_recovered,
    post_round_decision,
)
from agent_runner.cli.common import cfg_from_args
from agent_runner.clock import SYSTEM_CLOCK, Clock
from agent_runner.hooks import run_serve_startup_hooks
from agent_runner.lifecycle import PIDFile
from agent_runner.round_log import (
    ROUND_CURRENT_LINK,
    atomic_relink,
    next_round_num,
    prune_old_round_logs,
)


def _acquire_serve_lock(log_dir: Path) -> int | None:
    """Take a loop-lifetime exclusive flock on ``serve.lock`` so two ``serve``
    loops can't run on one project (the round-level ``agent-runner.lock`` only
    covers a single round, in the child). Returns the held fd, or None if another
    serve already holds it. The fd is closed by :func:`_release_serve_lock`, which
    releases the flock; it must NOT leak into the round subprocess (Popen's default
    close_fds=True keeps it out)."""
    fd = os.open(log_dir / "serve.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _release_serve_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


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


def _pause_poll(stop, stop_file, runnable_fn, sleep_fn, chunk_s) -> bool:
    """Chunked (<= chunk_s) sleep until ``runnable_fn()`` is True; break on
    ``stop["requested"]`` or ``stop_file``. Returns True iff a window opened (a
    stop / stop_file break returns False — the pause was interrupted, not resumed).
    Shared by both pause entry points; only the runnable predicate + sleep differ."""
    while not stop["requested"]:
        if stop_file is not None and stop_file.exists():
            return False
        if runnable_fn():
            return True
        sleep_fn(chunk_s)
    return False


def _maybe_pause_for_schedule(
    cfg,
    log_dir,
    stop,
    *,
    now_fn=schedule.now_in_zone,
    sleep_fn=SYSTEM_CLOCK.sleep,
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

    started = SYSTEM_CLOCK.monotonic()
    emit_schedule_paused(
        log_dir,
        active_window=decision.active_window or "",
        resume_at=decision.resume_at.isoformat() if decision.resume_at else "",
        timezone=sched.timezone or "local",
    )
    # #3: should_run (not evaluate) avoids the 8-day next_resume_at scan on every
    # poll; a stop / stop_file break returns False (interrupted, not resumed).
    if _pause_poll(
        stop,
        cfg.runtime.stop_file,
        lambda: schedule.should_run(
            now_fn(sched.timezone),
            run_windows=sched.run_windows,
            pause_windows=sched.pause_windows,
        ),
        sleep_fn,
        chunk_s,
    ):
        emit_schedule_resumed(log_dir, paused_for_s=int(SYSTEM_CLOCK.monotonic() - started))
    return True


# Sentinel returned by _select_and_gate: "we paused; caller should `continue`".
_PAUSED_CONTINUE = object()


def _phase_aware(cfg) -> bool:
    """True when per-phase scheduling governs this round selection.

    Both triggers (``phase_policy = "skip"``, any ``[phases.<name>.schedule]``)
    are new 0.2.9 syntax, so no pre-0.2.9 config is phase-aware — those take the
    unmodified legacy pause path and stay byte-identical."""
    return bool(cfg.phases.list) and (
        cfg.phases.phase_policy == "skip"
        or any(ov.schedule is not None for ov in cfg.phases.overrides.values())
    )


def _pause_until_selectable(
    cfg,
    log_dir,
    stop,
    round_num,
    sel,
    *,
    throttled_phases: frozenset[str] = frozenset(),
    wake_epoch: int | None = None,
    now_fn=schedule.now_in_zone,
    clock: Clock = SYSTEM_CLOCK,
    chunk_s: int = 30,
) -> None:
    """Phase-aware analogue of _maybe_pause_for_schedule: idle until any candidate
    phase's window opens. ``sel`` is the paused Selection already computed by the
    caller — its ``resume_*`` fields (all describing the earliest-opening candidate)
    make the schedule_paused payload coherent without recomputing. Polls with
    ``schedule.should_run`` to keep the resume scan out of the 30s poll. Always
    returns after pause/stop — the caller ``continue``s and re-selects from the top.

    ``throttled_phases`` are excluded from the window poll: a throttled phase's
    window is usually OPEN, so leaving it in would make ``should_run`` fire at once
    and busy-loop. ``wake_epoch`` (the throttle's reset_at) is an extra wake trigger
    so an all-throttled round resumes when the throttle clears even though no window
    ever opens. ``clock`` supplies epoch/sleep/monotonic (inject a ``FakeClock`` to
    pin the wake); ``now_fn`` stays a separate seam — the tz-aware datetime the pure
    schedule core needs, which tests monkeypatch by name."""
    candidates = [
        (p, cfg.profile_for(p).schedule)
        for p in phase_select.candidate_phases(cfg, round_num)
        if p not in throttled_phases
    ]
    started = clock.monotonic()
    emit_schedule_paused(
        log_dir,
        active_window=sel.active_window or "",
        resume_at=sel.resume_at.isoformat() if sel.resume_at else "",
        timezone=sel.resume_timezone or "local",
        phase=sel.resume_phase or "",
    )
    if _pause_poll(
        stop,
        cfg.runtime.stop_file,
        lambda: (
            (wake_epoch is not None and clock.epoch() >= wake_epoch)
            or any(
                schedule.should_run(
                    now_fn(sched.timezone),
                    run_windows=sched.run_windows,
                    pause_windows=sched.pause_windows,
                )
                for _p, sched in candidates
            )
        ),
        clock.sleep,
        chunk_s,
    ):
        emit_schedule_resumed(log_dir, paused_for_s=int(clock.monotonic() - started))


def _maybe_emit_recovered(log_dir) -> None:
    """Emit ``transient_error_recovered`` for every agent whose throttle cleared via
    skip-around (rotation, no back-off sleep) and so left no breadcrumb. Per-agent, so
    one agent recovering while a sibling is still throttled still gets its breadcrumb.
    Events-derived + dedup-safe (see ``pending_recovered``): the back-off path already
    emits its own recovered, so this stays quiet there and never double-emits."""
    for agent, classification, throttled_for_s in pending_recovered(log_dir):
        emit_transient_error_recovered(
            log_dir, classification=classification, agent=agent, throttled_for_s=throttled_for_s
        )


def _skip_around(cfg, args) -> bool:
    """True when this config routes around a throttled phase instead of the global
    back-off: phase-aware ``phase_policy = "skip"`` and not ``--ignore-schedule``.

    This is the ONLY mode that both defers a throttle to phase rotation AND emits
    the events-derived recovered breadcrumb — so gating both on it keeps every
    legacy path (no ``[phases]``, ``wait``, ``--ignore-schedule``) byte-identical
    to 0.2.9, which emitted a recovered only from the back-off sleep."""
    return _phase_aware(cfg) and cfg.phases.phase_policy == "skip" and not args.ignore_schedule


def _gate_throttle(cfg, log_dir, throttle, stop) -> Literal["proceed", "break"]:
    """Legacy single-throttle gate for the NON-skip regime (wait / back_off / stop).

    Returns ``"break"`` (stop the loop — throttle action is ``stop``, or a SIGTERM
    landed mid-back-off) or ``"proceed"`` (no throttle, or back_off already applied and
    resumed). The skip-policy regime uses :func:`_throttle_skip_context` instead, so
    this never sees a skip-around config — it neither defers nor emits a skip
    breadcrumb, keeping every pre-0.2.11 non-skip path byte-identical."""
    if throttle is None:
        _reset_counters()
        return "proceed"
    action = cfg.runtime.transient_error_action
    if action == "back_off":
        # Emits transient_error_recovered on resume; returns True (→ break) if a
        # SIGTERM cut the back-off short, leaving the throttle active and no breadcrumb.
        if _apply_back_off(log_dir, throttle, stop=stop):
            return "break"
    elif action == "stop":
        emit_rate_limit_stop(log_dir)
        return "break"
    # "skip" transient_error_action (distinct from phase_policy=skip): normal launch.
    return "proceed"


def _throttle_skip_context(cfg, log_dir) -> tuple[frozenset[str], int | None]:
    """Skip-policy throttle handling (multi-agent). Emits recovered breadcrumbs for any
    agent that cleared (even while siblings remain throttled), resets the exp-backoff
    counters when nothing is throttled, and returns ``(throttled_phases, wake_epoch)``
    for :func:`_select_and_gate`.

    Rate limits are per-provider, so selection routes around EVERY phase whose agent —
    the binary basename of ``command[0]``, which is the detector's ``agent`` label — is
    throttled. ``wake_epoch`` is the earliest reset among the blocking agents, so an
    all-throttled round wakes when the first one clears."""
    active = _active_throttles(log_dir)
    _maybe_emit_recovered(log_dir)  # per-agent, dedup-safe — runs even if throttles remain
    if not active:
        _reset_counters()
    throttled: set[str] = set()
    wake: int | None = None
    for phase in cfg.phases.list:
        st = active.get(Path(cfg.profile_for(phase).agent.command[0]).name)
        if st is not None:
            throttled.add(phase)
            wake = st.reset_at_epoch if wake is None else min(wake, st.reset_at_epoch)
    return frozenset(throttled), wake


def _round_throttle_gate(cfg, args, log_dir, stop) -> tuple[frozenset[str], int | None] | str:
    """Per-round throttle decision → ``(throttled_phases, wake_epoch)`` for selection,
    or the string ``"break"`` to stop the loop.

    Skip-policy configs route around every throttled agent's phases
    (:func:`_throttle_skip_context`); every other config takes the legacy
    single-throttle gate (wait / back_off / stop), which handles the throttle here and
    returns an empty throttled-phase set."""
    if _skip_around(cfg, args):
        return _throttle_skip_context(cfg, log_dir)
    throttle = _check_throttle_state(log_dir)
    if _gate_throttle(cfg, log_dir, throttle, stop) == "break":
        return "break"
    return frozenset(), None


def _select_and_gate(
    cfg,
    args,
    log_dir,
    stop,
    round_num,
    *,
    throttled_phases: frozenset[str] = frozenset(),
    wake_epoch: int | None = None,
):
    """Resolve the phase to launch this round, gating on schedule and on
    ``throttled_phases`` — the phases whose agent is currently throttled, injected by
    :func:`_round_throttle_gate` (empty for every non-skip config).

    Returns the phase name (``str``), ``None`` (no ``--phase``: legacy or
    --ignore-schedule), or the ``_PAUSED_CONTINUE`` sentinel meaning the caller
    paused and should ``continue`` from the loop top."""
    if args.ignore_schedule:
        return None  # rotation self-resolves in the round; no --phase, no gate
    if not _phase_aware(cfg):
        if _maybe_pause_for_schedule(cfg, log_dir, stop):
            return _PAUSED_CONTINUE
        return None
    # Pass the clock explicitly (call-time lookup) so tests can monkeypatch
    # schedule.now_in_zone; a default arg would capture the original at import.
    sel = phase_select.select_phase(
        cfg, round_num, throttled_phases=throttled_phases, now_fn=schedule.now_in_zone
    )
    if sel.paused:
        # Nothing runnable (every phase throttled or window-closed). With throttled
        # phases AND transient_error_action="stop", halt; otherwise pause until the
        # EARLIER of a sibling window opening or a throttle resetting (wake_epoch),
        # chunked + SIGTERM-responsive, then re-select. Empty throttled_phases + None
        # wake is the plain window pause — one call serves both.
        if throttled_phases and cfg.runtime.transient_error_action == "stop":
            emit_rate_limit_stop(log_dir)
            stop["requested"] = True
            return _PAUSED_CONTINUE
        _pause_until_selectable(
            cfg,
            log_dir,
            stop,
            round_num,
            sel,
            throttled_phases=throttled_phases,
            wake_epoch=wake_epoch,
            # Explicit call-time lookup (not the import-bound default) so tests can
            # monkeypatch schedule.now_in_zone; the clock default is a stable object
            # whose .sleep tests mutate in place, so it needs no call-time passing.
            now_fn=schedule.now_in_zone,
        )
        return _PAUSED_CONTINUE
    if sel.skipped:
        emit_schedule_phase_skipped(
            log_dir,
            round_num=round_num,
            skipped=sel.skipped,
            chosen=sel.phase,
            active_window=sel.active_window or "",
        )
    return sel.phase


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


def _apply_fresh_eyes(cfg, log_dir, round_num: int, round_env: dict) -> None:
    """Set the round's fresh-eyes env flag and emit the trigger event when due."""
    fresh_eyes = _is_fresh_eyes_round(round_num=round_num, every_n=cfg.runtime.fresh_eyes_every_n)
    round_env["AGENT_RUNNER_FRESH_EYES"] = "1" if fresh_eyes else "0"
    if fresh_eyes:
        emit_fresh_eyes_round_triggered(
            log_dir, round_num=round_num, every_n=cfg.runtime.fresh_eyes_every_n
        )


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

    serve_lock_fd = _acquire_serve_lock(log_dir)
    if serve_lock_fd is None:
        print(f"agent-runner serve already running for {cfg.runtime.work_dir}", file=sys.stderr)
        return 1

    if not run_serve_startup_hooks(cfg, log_dir):
        _release_serve_lock(serve_lock_fd)
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
    # Give-up stops (config_broken/crash_loop) return a distinct non-zero code the
    # systemd unit lists in RestartPreventExitStatus so they stay stopped; every
    # other stop (sentinel/stop_file/max_rounds/SIGTERM/once) is a clean exit 0.
    exit_code = 0

    try:
        pid_file.write(os.getpid())
        while not stop["requested"]:
            if check_self_terminated_sentinel(log_dir):
                break
            gate = _round_throttle_gate(cfg, args, log_dir, stop)
            if gate == "break":
                break
            throttled_phases, wake_epoch = gate
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
            phase_arg = _select_and_gate(
                cfg,
                args,
                log_dir,
                stop,
                round_num,
                throttled_phases=throttled_phases,
                wake_epoch=wake_epoch,
            )
            if phase_arg is _PAUSED_CONTINUE:
                continue
            if stop["requested"]:
                break  # SIGTERM landed during selection/back-off — don't spawn a round
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
            _apply_fresh_eyes(cfg, log_dir, round_num, round_env)
            round_log_path = log_dir / f"round-{round_num}.log"
            round_started = SYSTEM_CLOCK.monotonic()
            round_argv = [
                sys.executable,
                "-m",
                "agent_runner.cli",
                "--config",
                str(args.config),
                "round",
            ]
            if phase_arg is not None:
                round_argv += ["--phase", phase_arg]
            with round_log_path.open("w") as f:
                r = subprocess.run(round_argv, env=round_env, stdout=f, stderr=subprocess.STDOUT)
            round_duration_s = SYSTEM_CLOCK.monotonic() - round_started
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
                # Crash-loop breaker excuses a fast exit only when THE AGENT THAT JUST
                # RAN is throttled — a sibling agent's throttle must not mask its crash.
                throttle_active=Path(cfg.profile_for(phase_arg).agent.command[0]).name
                in _active_throttles(log_dir),
                consecutive=consecutive_crashes,
                restart_delay_s=cfg.runtime.restart_delay_s,
            )
            if action == "config_broken":
                emit_config_broken(log_dir, reason="startup battery permanent failure")
                exit_code = PERMANENT_CONFIG_EXIT
                break
            if action == "crash_loop":
                emit_crash_loop(
                    log_dir,
                    consecutive=consecutive_crashes,
                    exit_code=r.returncode,
                    log_path=round_log_path,
                )
                exit_code = CRASH_LOOP_EXIT
                break
            if args.once or stop["requested"]:
                break
            # Chunked so a SIGTERM during a long restart delay lands within one chunk.
            _interruptible_sleep(delay, stop)
    finally:
        pid_file.unlink()
        _release_serve_lock(serve_lock_fd)
    return exit_code
