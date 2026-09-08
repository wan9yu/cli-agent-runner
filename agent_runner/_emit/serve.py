"""Event emission wrappers — supervisor control plane: why serve paused,
stopped, or broke, and config/schedule lifecycle. Re-exported from
``agent_runner._emit`` (the package facade) — see its docstring.
"""

from __future__ import annotations

from pathlib import Path


def emit_rate_limit_stop(log_dir: Path) -> None:
    """Emit ``agent_self_terminated`` with reason ``rate_limit`` (serve_cmd wrapper)."""
    from agent_runner import events

    events.emit(log_dir, events.SELF_TERMINATED, reason="rate_limit")


def emit_max_rounds_reached(log_dir: Path, *, rounds_completed: int, max_rounds: int) -> None:
    """Emit max_rounds_reached event (serve_cmd wrapper; avoids direct events import)."""
    from agent_runner.events import MAX_ROUNDS_REACHED, emit

    emit(log_dir, MAX_ROUNDS_REACHED, rounds_completed=rounds_completed, max_rounds=max_rounds)


def emit_config_broken(log_dir: Path, *, reason: str) -> None:
    """Emit config_broken (serve gave up on a permanent, non-self-healing failure —
    a startup-battery check, or any other ConfigError-classified round exit)."""
    from agent_runner.events import CONFIG_BROKEN, emit

    emit(log_dir, CONFIG_BROKEN, reason=reason)


def _emit_giveup(
    log_dir: Path, kind: str, *, consecutive: int, exit_code: int, log_path: Path
) -> None:
    """Shared body for the give-up events (serve stopped/broke after consecutive
    failures of one kind): capture a redacted tail of the round log as ``reason``
    so the recurring failure can be inspected (or later classified into a
    transient bucket), and emit ``kind`` with the standard give-up payload.
    """
    from agent_runner._redact import redact_secrets
    from agent_runner.events import emit

    try:
        reason = redact_secrets(log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
    except OSError:
        reason = ""
    emit(log_dir, kind, consecutive=consecutive, exit_code=exit_code, reason=reason)


def emit_crash_loop(log_dir: Path, *, consecutive: int, exit_code: int, log_path: Path) -> None:
    """Emit crash_loop (serve stopped after consecutive unknown short crashes).

    Captures the failure reason — a redacted tail of the round log — so a
    recurring unknown crash can later be classified into a transient bucket.
    """
    from agent_runner.events import CRASH_LOOP

    _emit_giveup(
        log_dir, CRASH_LOOP, consecutive=consecutive, exit_code=exit_code, log_path=log_path
    )


def emit_mem_loop(log_dir: Path, *, consecutive: int, exit_code: int, log_path: Path) -> None:
    """Emit mem_loop (serve gave up after consecutive mid-round memory-pressure
    terminations — the 0.2.15 coma-preventer's give-up cap). Distinct from
    crash_loop: this is a break-then-restart, not a deliberate stop, so it is
    deliberately absent from the unit's RestartPreventExitStatus.

    Captures the failure reason — a redacted tail of the round log — same as
    crash_loop, so an operator can see what the round was doing when the host
    ran out of memory."""
    from agent_runner.events import MEM_LOOP

    _emit_giveup(log_dir, MEM_LOOP, consecutive=consecutive, exit_code=exit_code, log_path=log_path)


def emit_mem_loop_persistent(
    log_dir: Path, *, consecutive: int, exit_code: int, log_path: Path
) -> None:
    """Emit mem_loop_persistent (serve STOPS for real — 0.2.16 Task 5 cross-restart
    convergence: mem_loop itself kept recurring across restarts within the
    escalation window, so systemd is told to stop rather than respawn into the
    identical loop forever). Distinct from mem_loop: this IS a deliberate stop,
    like crash_loop/config_broken, so it belongs in the unit's
    RestartPreventExitStatus.

    ``consecutive`` counts mem_loop EPISODES within the persistence window
    (up to ``_serve_policy._MEM_LOOP_PERSIST_THRESHOLD``) — NOT mid-round
    terminations within one episode, which is mem_loop's own ``consecutive``
    (up to ``MEM_LOOP_THRESHOLD``). Same redacted-log-tail reason capture as
    crash_loop/mem_loop."""
    from agent_runner.events import MEM_LOOP_PERSISTENT

    _emit_giveup(
        log_dir,
        MEM_LOOP_PERSISTENT,
        consecutive=consecutive,
        exit_code=exit_code,
        log_path=log_path,
    )


def emit_stalled_no_progress(
    log_dir: Path, *, consecutive: int, exit_code: int, log_path: Path
) -> None:
    """Emit stalled_no_progress (serve gave up after consecutive clean-but-
    no-progress rounds -- 0.2.16 Task 6). A round that exits 0 fast with no
    ``agent_usage_recorded`` never reached the model (pi, and CLIs like it,
    exit 0 on a provider failure) -- ``_round_ok = exit_code == 0``
    (api_types.py) reads that as clean, so without this breaker it is a fast,
    invisible, unclassified-failure spin, no different in kind from an
    unknown short crash except for the exit code it hides behind.

    Deliberately reuses ``CRASH_LOOP_EXIT`` (75), not a new exit code: this is
    the SAME give-up verdict as crash_loop ("an unknown failure kept
    recurring, stop for real") reached via a different signal (no usage
    instead of a non-zero exit) -- not a new failure class, so it needs no new
    entry in the systemd unit's ``RestartPreventExitStatus``. Same redacted-
    log-tail reason capture as crash_loop/mem_loop."""
    from agent_runner.events import STALLED_NO_PROGRESS

    _emit_giveup(
        log_dir,
        STALLED_NO_PROGRESS,
        consecutive=consecutive,
        exit_code=exit_code,
        log_path=log_path,
    )


def emit_config_migrated(
    log_dir: Path, *, applied: list[str], manual: list[str], path: str
) -> None:
    """Emit config_migrated when `migrate`/`upgrade` rewrites the config."""
    from agent_runner.events import CONFIG_MIGRATED, emit

    emit(log_dir, CONFIG_MIGRATED, applied=applied, manual=manual, path=path)


def emit_stop_file_detected(
    log_dir: Path, *, stop_file: Path, content: str, rounds_completed: int
) -> None:
    """Centralises emission so cli/serve_cmd.py need not import agent_runner.events directly."""
    from agent_runner.events import STOP_FILE_DETECTED, emit

    emit(
        log_dir,
        STOP_FILE_DETECTED,
        stop_file=str(stop_file),
        content=content,
        rounds_completed=rounds_completed,
    )


def emit_schedule_paused(
    log_dir: Path, *, active_window: str, resume_at: str, timezone: str, phase: str = ""
) -> None:
    """Emit schedule_paused when the serve loop enters a configured pause window.

    ``phase`` is the phase the supervisor is waiting for on a phase-aware pause;
    it is omitted from the payload when empty so the legacy (non-phase) pause
    stays byte-identical to 0.2.7."""
    from agent_runner.events import SCHEDULE_PAUSED, emit

    fields = {"active_window": active_window, "resume_at": resume_at, "timezone": timezone}
    if phase:
        fields["phase"] = phase
    emit(log_dir, SCHEDULE_PAUSED, **fields)


def emit_schedule_phase_skipped(
    log_dir: Path, *, round_num: int, skipped: list[str], chosen: str | None, active_window: str
) -> None:
    """Emit schedule_phase_skipped when phase_policy=skip steps over closed phases
    to reach the first runnable one this round."""
    from agent_runner.events import SCHEDULE_PHASE_SKIPPED, emit

    emit(
        log_dir,
        SCHEDULE_PHASE_SKIPPED,
        round_num=round_num,
        skipped=skipped,
        chosen=chosen,
        active_window=active_window,
    )


def emit_schedule_resumed(log_dir: Path, *, paused_for_s: int) -> None:
    """Emit schedule_resumed when the serve loop exits a pause window."""
    from agent_runner.events import SCHEDULE_RESUMED, emit

    emit(log_dir, SCHEDULE_RESUMED, paused_for_s=paused_for_s)
