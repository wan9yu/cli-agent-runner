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
    "emit_anomaly_repetitive_tool",
    "emit_fresh_eyes_round_triggered",
    "emit_max_rounds_reached",
    "emit_rate_limit_stop",
    "emit_round_grace_extended",
    "emit_round_grace_kill",
    "emit_round_progress",
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


def emit_max_rounds_reached(log_dir: Path, *, rounds_completed: int, max_rounds: int) -> None:
    """Emit max_rounds_reached event (serve_cmd wrapper; avoids direct events import)."""
    from agent_runner.events import MAX_ROUNDS_REACHED, emit

    emit(log_dir, MAX_ROUNDS_REACHED, rounds_completed=rounds_completed, max_rounds=max_rounds)


def emit_config_broken(log_dir: Path, *, reason: str) -> None:
    """Emit config_broken (serve stopped on a permanent startup-battery failure)."""
    from agent_runner.events import CONFIG_BROKEN, emit

    emit(log_dir, CONFIG_BROKEN, reason=reason)


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
    from agent_runner._redact import redact_secrets
    from agent_runner.events import TRANSIENT_ERROR_DETECTED, emit

    raw = redact_secrets(raw)
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
    cache_creation_tokens: int = 0,
    tool_call_count: int = 0,
    phase: str = "",
    success: bool = True,
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
    - ``cache_creation_tokens``: claude only — ``usage.cache_creation_input_tokens``,
      independent count from ``cached_tokens`` (cache_read). Billed at ~25% premium
      over fresh input per Anthropic pricing. Gemini has no equivalent → 0.
    - ``tool_call_count``: number of tool invocations the agent made in the round.
      Claude: count of ``tool_use`` content blocks across all assistant events.
      Gemini: ``stats.tool_calls``.
    - ``phase``: phase label from HookContext (e.g. "planning"); empty string when None.
    - ``success``: True when exit_code == 0 and not timed_out.
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
        cache_creation_tokens=cache_creation_tokens,
        tool_call_count=tool_call_count,
        phase=phase,
        success=success,
    )


def emit_round_progress(
    log_dir: Path,
    *,
    round_num: int,
    log_size_kb: int,
    last_write_age_s: int,
    wall_age_s: int,
) -> None:
    """Mid-round heartbeat event when round_progress_interval_s > 0.

    Emitted periodically during a live round to surface visibility on long
    rounds: log_size_kb shows writing activity; last_write_age_s and wall_age_s
    together distinguish "agent thinking" from "agent stuck".
    """
    from agent_runner.events import ROUND_PROGRESS, emit

    emit(
        log_dir,
        ROUND_PROGRESS,
        round_num=round_num,
        log_size_kb=log_size_kb,
        last_write_age_s=last_write_age_s,
        wall_age_s=wall_age_s,
    )


def emit_round_grace_kill(
    log_dir: Path,
    *,
    round_num: int,
    grace_s: int,
    live_children: list[dict] | None = None,
) -> None:
    """Emit when the subprocess was killed because the grace-after-result timer
    expired AND the agent's process group had no live worker processes left
    (a genuine hang). Distinct from round_grace_extended (grace elapsed but a
    worker was still running) and round_timeout_kill (wall-clock exceeded).

    live_children: list of ``{"name": <exe basename>, "pid": <int>}`` dicts
        (0.1.40+; previously list of cmdline strings).
    """
    from agent_runner.events import ROUND_GRACE_KILL, emit

    emit(
        log_dir,
        ROUND_GRACE_KILL,
        round_num=round_num,
        grace_s=grace_s,
        live_children=live_children or [],
    )


def emit_round_grace_extended(
    log_dir: Path,
    *,
    round_num: int,
    grace_s: int,
    live_children: list[dict],
    ignored_children: list[dict] | None = None,
) -> None:
    """Emit when the grace-after-result timer expired but the agent still had
    live worker processes (e.g. a backgrounded build), so the round was NOT
    killed; it continues until it finishes or hits round_timeout_s.

    live_children: list of ``{"name": <exe basename>, "pid": <int>}`` dicts
        (0.1.40+; previously list of cmdline strings).
    ignored_children: list of ``{"name": ..., "pid": ..., "matched": <pattern>}``
        dicts for children that matched a grace_kill_ignore_patterns entry
        and were excluded from the liveness count (0.1.40+; previously cmdline strings).
    """
    from agent_runner.events import ROUND_GRACE_EXTENDED, emit

    emit(
        log_dir,
        ROUND_GRACE_EXTENDED,
        round_num=round_num,
        grace_s=grace_s,
        live_children=live_children,
        ignored_children=ignored_children or [],
    )


def emit_anomaly_repetitive_tool(
    log_dir: Path,
    *,
    round_num: int,
    tool_name: str,
    target: str | None,
    count: int,
    window: int,
) -> None:
    """Emit when claude plugin detects the same (tool, target) tuple repeated
    >= threshold times in a sliding window of tool-call events.

    Claude-only (gemini JSONL stats summary does not expose per-tool events).
    Default OFF: both anomaly_repetitive_window and anomaly_repetitive_threshold
    must be > 0 in [monitor] config to activate.
    """
    from agent_runner.events import ANOMALY_REPETITIVE_TOOL, emit

    emit(
        log_dir,
        ANOMALY_REPETITIVE_TOOL,
        round_num=round_num,
        tool_name=tool_name,
        target=target,
        count=count,
        window=window,
    )


def emit_transient_error_backoff_capped(
    log_dir: Path,
    *,
    classification: str,
    agent: str,
    requested_sleep_s: int,
    applied_sleep_s: int,
    original_reset_at_epoch: int | None = None,
    applied_reset_at_epoch: int | None = None,
    consecutive_count: int | None = None,
    capped_by_absolute_max: bool | None = None,
) -> None:
    """Emit when supervisor adjusts the plugin-emitted transient back-off.

    Fires in two cases:
    1. **Exp backoff applied** (0.1.33+): estimated-class transient errors
       (`rate_limit_model` / `api_transient_5xx` / `api_timeout`) doubled
       on consecutive failures. ``consecutive_count`` > 1, multiplier > 1×.
    2. **Defensive cap hit** (0.1.20+): malformed `reset_at_epoch` or the
       30-min absolute cap clipped the wait. ``capped_by_absolute_max`` True.

    Fields ``original_reset_at_epoch`` / ``applied_reset_at_epoch`` /
    ``consecutive_count`` / ``capped_by_absolute_max`` are 0.1.33+. Older
    callers that pass only the first 4 kwargs continue to work; the new
    fields are omitted from the payload when None.
    """
    from agent_runner.events import TRANSIENT_ERROR_BACKOFF_CAPPED, emit

    kwargs: dict = {
        "classification": classification,
        "agent": agent,
        "requested_sleep_s": requested_sleep_s,
        "applied_sleep_s": applied_sleep_s,
    }
    if original_reset_at_epoch is not None:
        kwargs["original_reset_at_epoch"] = original_reset_at_epoch
    if applied_reset_at_epoch is not None:
        kwargs["applied_reset_at_epoch"] = applied_reset_at_epoch
    if consecutive_count is not None:
        kwargs["consecutive_count"] = consecutive_count
    if capped_by_absolute_max is not None:
        kwargs["capped_by_absolute_max"] = capped_by_absolute_max

    emit(log_dir, TRANSIENT_ERROR_BACKOFF_CAPPED, **kwargs)
