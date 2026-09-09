"""Event emission wrappers — the agent axis: what a per-CLI plugin reports
(transient errors, auth failures, usage, anomalies) and the throttle
response. Re-exported from ``agent_runner._emit`` (the package facade) —
see its docstring.
"""

from __future__ import annotations

from pathlib import Path


def emit_transient_error_detected(
    log_dir: Path,
    *,
    classification: str,
    agent: str,
    reset_at_epoch: int,
    round_num: int,
    raw: str,
    phase: str = "",
) -> None:
    """Emit detection of a transient agent error (rate limit / 5xx / timeout).

    ``phase`` is the rotation phase the failing round ran under ("" when the
    config has no ``[phases]``). It lets a ``phase_policy = "skip"`` serve loop
    step over *this* phase while a healthy sibling keeps running."""
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
        phase=phase,
    )


def emit_agent_auth_error_detected(
    log_dir: Path,
    *,
    round_num: int,
    agent: str,
    raw: str,
) -> None:
    """Emit an authentication/authorization failure the agent itself reported.

    Contract: emitted by a per-CLI plugin only when the agent's OWN structured
    output names the failure (e.g. an HTTP 401 in its JSON event stream). That
    is certain evidence, unlike the monitor's ``oauth_fail`` text heuristic,
    which scans free-text log tails and therefore needs a nonzero-exit shield
    against prose that merely mentions "401". The monitor counts a round
    carrying this event without that shield — which is what makes an auth loop
    visible for a CLI that exits 0 on provider failure.

    No back-off partner event: an auth failure is permanent until an operator
    fixes the credential, so it is deliberately not a transient classification.
    """
    from agent_runner._redact import redact_secrets
    from agent_runner.events import AGENT_AUTH_ERROR_DETECTED, emit

    emit(
        log_dir,
        AGENT_AUTH_ERROR_DETECTED,
        round_num=round_num,
        agent=agent,
        raw=redact_secrets(raw),
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
    deferred to consumers and the capability layer.

    Field semantics:
    - ``input_tokens``: net non-cached input (both claude and gemini emit net;
      total throughput = input_tokens + cached_tokens).
    - ``cost_usd``: USD cost for the round, or None when CLI doesn't expose it
      (gemini has no cost field; claude exposes total_cost_usd).
    - ``models_breakdown``: only populated when a round used multiple models
      (gemini multi-model rounds). None for claude (always single-model).
    - ``cache_creation_tokens``: claude only — ``usage.cache_creation_input_tokens``,
      independent count from ``cached_tokens`` (cache_read). Priced differently
      from fresh input by the provider; the framework records the count only and
      never interprets cost (no price tables here — see ``cost_usd`` above for the
      one cost figure the framework passes through verbatim). Gemini has no
      equivalent → 0.
    - ``tool_call_count``: number of tool invocations the agent made in the round.
      Claude: count of ``tool_use`` content blocks across all assistant events.
      Gemini: ``stats.tool_calls``.
    - ``phase``: phase label from HookContext (e.g. "planning"); empty string when None.
    - ``success``: the supervisor's clean-exit predicate (``RoundResult.ok``).
      A plugin MAY additionally fold in the agent's own terminal verdict when
      its CLI's exit code is unreliable — pi does, because pi exits 0 on
      provider failure.
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
    1. **Exp backoff applied**: estimated-class transient errors
       (`rate_limit_model` / `api_transient_5xx` / `api_timeout`) doubled
       on consecutive failures. ``consecutive_count`` > 1, multiplier > 1×.
    2. **Defensive cap hit**: malformed `reset_at_epoch` or the
       30-min absolute cap clipped the wait. ``capped_by_absolute_max`` True.

    Fields ``original_reset_at_epoch`` / ``applied_reset_at_epoch`` /
    ``consecutive_count`` / ``capped_by_absolute_max`` are newer fields. Older
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
