"""Event emission wrappers — extracted from api.py for module-size hygiene.

All wrappers are re-exported from agent_runner.api for backward compatibility.
Plugins and supervisor should continue importing from agent_runner.api
(e.g. ``from agent_runner.api import emit_transient_error_detected``).

Each wrapper exists to keep cli/serve_cmd.py from importing agent_runner.events
directly — preserves the 0.1.21 architecture invariant. Local-import pattern
inside each wrapper body keeps agent_runner.api import-cheap.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "emit_agent_usage_recorded",
    "emit_fresh_eyes_round_triggered",
    "emit_max_rounds_reached",
    "emit_rate_limit_backoff_capped",
    "emit_rate_limit_recovered",
    "emit_rate_limit_rejected",
    "emit_rate_limit_stop",
    "emit_round_substrate_after",
    "emit_round_substrate_before",
    "emit_stop_file_detected",
    "emit_transient_error_backoff_capped",
    "emit_transient_error_detected",
    "emit_transient_error_recovered",
]


def emit_rate_limit_stop(log_dir: Path) -> None:
    """Emit ``agent_self_terminated`` with reason ``rate_limit`` (serve_cmd wrapper)."""
    from agent_runner import events

    events.emit(log_dir, events.SELF_TERMINATED, reason="rate_limit")


def emit_rate_limit_rejected(
    log_dir: Path,
    *,
    agent: str,
    reset_at_epoch: int,
    limit_type: str,
    round_num: int,
    raw: str,
) -> None:
    """Emit 0.1.20 rate_limit_rejected event (kept as alias; dual-emitted through 0.1.23)."""
    from agent_runner.events import RATE_LIMIT_REJECTED, emit

    emit(
        log_dir,
        RATE_LIMIT_REJECTED,
        agent=agent,
        reset_at_epoch=reset_at_epoch,
        limit_type=limit_type,
        round_num=round_num,
        raw=raw,
    )


def emit_rate_limit_recovered(
    log_dir: Path,
    *,
    agent: str,
    throttled_for_s: int,
    limit_type: str,
) -> None:
    """Emit 0.1.20 rate_limit_recovered event (kept as alias; dual-emitted through 0.1.23)."""
    from agent_runner.events import RATE_LIMIT_RECOVERED, emit

    emit(
        log_dir,
        RATE_LIMIT_RECOVERED,
        agent=agent,
        throttled_for_s=throttled_for_s,
        limit_type=limit_type,
    )


def emit_rate_limit_backoff_capped(
    log_dir: Path,
    *,
    agent: str,
    requested_sleep_s: int,
    applied_sleep_s: int,
) -> None:
    """Emit 0.1.20 rate_limit_backoff_capped event (kept as alias; dual-emitted through 0.1.23)."""
    from agent_runner.events import RATE_LIMIT_BACKOFF_CAPPED, emit

    emit(
        log_dir,
        RATE_LIMIT_BACKOFF_CAPPED,
        agent=agent,
        requested_sleep_s=requested_sleep_s,
        applied_sleep_s=applied_sleep_s,
    )


def emit_max_rounds_reached(log_dir: Path, *, rounds_completed: int, max_rounds: int) -> None:
    """Emit max_rounds_reached event (serve_cmd wrapper; avoids direct events import)."""
    from agent_runner.events import MAX_ROUNDS_REACHED, emit

    emit(log_dir, MAX_ROUNDS_REACHED, rounds_completed=rounds_completed, max_rounds=max_rounds)


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


def emit_round_substrate_before(
    log_dir: Path, *, round_num: int, git_head: str | None, paths_hash: str | None
) -> None:
    """Emit pre-round substrate fingerprint (git HEAD + optional file hash)."""
    from agent_runner.events import ROUND_SUBSTRATE_BEFORE, emit

    emit(
        log_dir,
        ROUND_SUBSTRATE_BEFORE,
        round_num=round_num,
        git_head=git_head,
        paths_hash=paths_hash,
    )


def emit_round_substrate_after(
    log_dir: Path, *, round_num: int, git_head: str | None, paths_hash: str | None
) -> None:
    """Emit post-round substrate fingerprint (git HEAD + optional file hash)."""
    from agent_runner.events import ROUND_SUBSTRATE_AFTER, emit

    emit(
        log_dir,
        ROUND_SUBSTRATE_AFTER,
        round_num=round_num,
        git_head=git_head,
        paths_hash=paths_hash,
    )


def emit_fresh_eyes_round_triggered(log_dir: Path, *, round_num: int, every_n: int) -> None:
    """Emit fresh-eyes signal trigger event (only on triggered rounds)."""
    from agent_runner.events import FRESH_EYES_ROUND_TRIGGERED, emit

    emit(
        log_dir,
        FRESH_EYES_ROUND_TRIGGERED,
        round_num=round_num,
        every_n=every_n,
    )


def emit_transient_error_detected(
    log_dir: Path,
    *,
    classification: str,
    agent: str,
    reset_at_epoch: int,
    round_num: int,
    raw: str,
) -> None:
    """Emit detection of a transient agent error (rate limit / 5xx / timeout)."""
    from agent_runner.events import TRANSIENT_ERROR_DETECTED, emit

    emit(
        log_dir,
        TRANSIENT_ERROR_DETECTED,
        classification=classification,
        agent=agent,
        reset_at_epoch=reset_at_epoch,
        round_num=round_num,
        raw=raw,
    )


def emit_transient_error_recovered(
    log_dir: Path,
    *,
    classification: str,
    agent: str,
    throttled_for_s: int,
) -> None:
    """Emit recovery from a transient error back-off (right before resuming)."""
    from agent_runner.events import TRANSIENT_ERROR_RECOVERED, emit

    emit(
        log_dir,
        TRANSIENT_ERROR_RECOVERED,
        classification=classification,
        agent=agent,
        throttled_for_s=throttled_for_s,
    )


def emit_agent_usage_recorded(
    log_dir: Path,
    *,
    agent: str,
    model: str,
    round_num: int,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    cost_usd: float | None,
    duration_ms: int,
    models_breakdown: dict[str, dict[str, int]] | None = None,
) -> None:
    """Emit per-round usage record from a CLI plugin.

    Raw data only — aggregation (totals, projections, budget warnings) is
    deferred to consumers and the 0.1.25 capability layer.

    Field semantics:
    - ``input_tokens``: net non-cached input (both claude and gemini emit net;
      total throughput = input_tokens + cached_tokens).
    - ``cost_usd``: USD cost for the round, or None when CLI doesn't expose it
      (gemini has no cost field; claude exposes total_cost_usd).
    - ``models_breakdown``: only populated when a round used multiple models
      (gemini multi-model rounds). None for claude (always single-model).
    """
    from agent_runner.events import AGENT_USAGE_RECORDED, emit

    emit(
        log_dir,
        AGENT_USAGE_RECORDED,
        agent=agent,
        model=model,
        round_num=round_num,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        models_breakdown=models_breakdown,
    )


def emit_transient_error_backoff_capped(
    log_dir: Path,
    *,
    classification: str,
    agent: str,
    requested_sleep_s: int,
    applied_sleep_s: int,
) -> None:
    """Emit defensive event when computed back-off exceeded 8h cap."""
    from agent_runner.events import TRANSIENT_ERROR_BACKOFF_CAPPED, emit

    emit(
        log_dir,
        TRANSIENT_ERROR_BACKOFF_CAPPED,
        classification=classification,
        agent=agent,
        requested_sleep_s=requested_sleep_s,
        applied_sleep_s=applied_sleep_s,
    )
