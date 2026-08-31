"""Built-in post_round_hook: classify claude transient errors from JSONL output.

Classifies into 4 buckets (rate_limit_account / rate_limit_model /
api_transient_5xx / api_timeout) and emits transient_error_detected events
with computed reset_at_epoch. Supervisor consumes the event.

Also emits agent_usage_recorded per-round with token/cost data from the
claude result event (0.1.24+).

Module name is historical: the original 0.1.20 single-purpose
rate-limit detector was generalized to multi-classification in 0.1.23
(class + entry-point renamed to `claude_error_detector`; module path kept).
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Any

from agent_runner.api import (
    emit_agent_usage_recorded,
    emit_anomaly_repetitive_tool,
    emit_transient_error_detected,
)
from agent_runner.builtin_plugins._constants import (
    _BACK_OFF_DEFAULTS,
    _RAW_CAP,
    classify_transient_status,
    json_events,
)
from agent_runner.clock import SYSTEM_CLOCK
from agent_runner.hooks import HookContext, register_post_round_hook


class ClaudeErrorDetector:
    """Classify claude transient errors; emit transient_error_detected + usage events."""

    name = "claude_error_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_binary != "claude":
            return
        log_path = ctx.agent_log_path
        if log_path is None or not log_path.exists():
            return
        parsed = _parse_claude_log(
            log_path,
            anomaly_window=ctx.anomaly_repetitive_window,
            anomaly_threshold=ctx.anomaly_repetitive_threshold,
        )

        if parsed.get("transient_error"):
            self._safe_emit(
                "transient_error",
                lambda: emit_transient_error_detected(
                    ctx.log_dir,
                    round_num=ctx.round_num,
                    phase=ctx.phase or "",
                    **parsed["transient_error"],
                ),
            )

        if parsed.get("usage"):
            self._safe_emit(
                "usage",
                lambda: emit_agent_usage_recorded(
                    ctx.log_dir,
                    round_num=ctx.round_num,
                    phase=ctx.phase or "",
                    success=result.ok,
                    **parsed["usage"],
                ),
            )

        if parsed.get("anomaly"):
            self._safe_emit(
                "anomaly",
                lambda: emit_anomaly_repetitive_tool(
                    ctx.log_dir, round_num=ctx.round_num, **parsed["anomaly"]
                ),
            )

    @staticmethod
    def _safe_emit(label: str, fn: Any) -> None:
        """Emit one payload in isolation — plugin hooks run as an all-or-nothing unit,
        so a single bad emit (e.g. a downstream write failure) must not void the
        other emits already computed from the same parsed round."""
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one failed emit must not void the siblings
            warnings.warn(f"claude {label} emit failed: {type(e).__name__}: {e}", stacklevel=2)


def _extract_tool_target(tool_input: Any) -> str | None:
    """Best-effort primary-input extraction for repetition detection."""
    if not isinstance(tool_input, dict):
        return None
    for key in ("file_path", "path", "pattern", "command"):
        v = tool_input.get(key)
        if isinstance(v, str):
            return v[:100]  # truncate long commands
    return None


def _detect_repetitive_tool(
    tool_calls: list[tuple[str, str | None]],
    *,
    window: int,
    threshold: int,
) -> dict | None:
    """Slide a window of size ``window`` over tool_calls; return anomaly dict
    when any (tool_name, target) tuple appears >= threshold times."""
    if window <= 0 or threshold <= 0 or len(tool_calls) < threshold:
        return None
    from collections import Counter

    n = len(tool_calls)
    for start in range(max(0, n - window), n - threshold + 1):
        window_slice = tool_calls[start : start + window]
        if not window_slice:
            continue
        counts = Counter(window_slice)
        most_common_tuple, most_common_count = counts.most_common(1)[0]
        if most_common_count >= threshold:
            return {
                "tool_name": most_common_tuple[0],
                "target": most_common_tuple[1],
                "count": most_common_count,
                "window": window,
            }
    return None


def _parse_claude_log(
    log_path: Path,
    *,
    anomaly_window: int = 0,
    anomaly_threshold: int = 0,
) -> dict[str, Any]:
    """Scan the JSON tail window for rate_limit/result/assistant events.

    Returns dict with optional 'transient_error', 'usage', and 'anomaly' keys.
    anomaly_window/anomaly_threshold: when both > 0, slide a window over
    (tool_name, target) tuples; populate 'anomaly' if threshold reached.
    """
    rate_limit_info: dict | None = None
    result_event: dict | None = None
    assistant_model: str | None = None
    tool_calls: list[tuple[str, str | None]] = []
    for event in json_events(log_path):
        event_type = event.get("type")
        if event_type == "rate_limit_event":
            rli = event.get("rate_limit_info")
            if isinstance(rli, dict) and rli.get("status") == "rejected":
                rate_limit_info = rli
        elif event_type == "result":
            result_event = event
        elif event_type == "assistant":
            msg = event.get("message", {})
            model_val = msg.get("model") if isinstance(msg, dict) else None
            if model_val:
                assistant_model = str(model_val)
            content = msg.get("content", []) if isinstance(msg, dict) else []
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        tool_calls.append(
                            (str(c.get("name", "?")), _extract_tool_target(c.get("input", {})))
                        )

    out: dict[str, Any] = {}

    error_payload = _classify_transient_error(rate_limit_info, result_event)
    if error_payload is not None:
        out["transient_error"] = error_payload

    if result_event is not None:
        usage_payload = _extract_usage(
            result_event, model=assistant_model, tool_call_count=len(tool_calls)
        )
        if usage_payload is not None:
            out["usage"] = usage_payload

    anomaly = _detect_repetitive_tool(
        tool_calls, window=anomaly_window, threshold=anomaly_threshold
    )
    if anomaly is not None:
        out["anomaly"] = anomaly

    return out


def _as_int(value: Any, fallback: int) -> int:
    """Coerce a foreign numeric field to int; a present-null, non-numeric, or
    non-finite (NaN/Infinity — both valid ``json.loads`` float tokens) value
    degrades to ``fallback`` instead of raising and voiding the round. Shared
    guard behind ``_as_epoch`` (reset-time fields) and ``_extract_usage``
    (token/duration counts) — never fabricates a value beyond the fallback."""
    if isinstance(value, bool) or value is None:
        return fallback
    if isinstance(value, float) and not math.isfinite(value):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback


def _as_epoch(value: Any, fallback: int) -> int:
    """Coerce a foreign ``resetsAt`` to an epoch int; see ``_as_int`` for the guard."""
    return _as_int(value, fallback)


def _classify_transient_error(
    rate_limit_info: dict | None, result_event: dict | None
) -> dict | None:
    """Claude-specific precedence: an account-level rate_limit_event outranks the
    result event's ``api_error_status``, which is handed to the shared ladder.
    """
    if rate_limit_info is not None and rate_limit_info.get("rateLimitType") == "five_hour":
        return {
            "classification": "rate_limit_account",
            "agent": "claude",
            "reset_at_epoch": _as_epoch(
                rate_limit_info.get("resetsAt"), int(SYSTEM_CLOCK.epoch()) + 300
            ),
            "raw": str((result_event or {}).get("result", ""))[:_RAW_CAP],
        }
    # rate_limit_event with null/other rateLimitType falls through to status-based
    # classification below.
    if result_event is None or result_event.get("is_error") is not True:
        return None
    classification = classify_transient_status(result_event.get("api_error_status"))
    if classification is None:
        return None
    return _classify(classification, str(result_event.get("result", ""))[:_RAW_CAP])


def _extract_usage(result_event: dict, *, model: str | None, tool_call_count: int) -> dict | None:
    """Extract usage payload from claude result event.

    Returns None if no usage field present.

    Semantic note:
    - ``input_tokens`` is the NET fresh input — Anthropic's ``usage.input_tokens``
      already excludes ``cache_read_input_tokens`` and ``cache_creation_input_tokens``
      (they're independent counts). Earlier 0.1.24 simplify pass incorrectly
      subtracted cached from input; 0.1.26 reverts to the correct direct read.
    - ``cached_tokens`` is cache reads only (``cache_read_input_tokens``).
    - ``cache_creation_tokens`` is ``cache_creation_input_tokens`` (write cost,
      billed at ~25% premium over fresh input per Anthropic pricing).
    - ``models_breakdown`` always None for claude (single-model per round);
      only populated by gemini multi-model rounds.
    - ``model`` from caller — ``_parse_claude_log`` tracks the latest
      ``assistant.message.model`` event; claude's terminal ``result`` event
      has no model field (lives on ``assistant`` events).
    """
    usage = result_event.get("usage")
    if not usage:
        return None
    return {
        "agent": "claude",
        "model": model or "unknown",
        "input_tokens": _as_int(usage.get("input_tokens"), 0),
        "output_tokens": _as_int(usage.get("output_tokens"), 0),
        "cached_tokens": _as_int(usage.get("cache_read_input_tokens"), 0),
        "cache_creation_tokens": _as_int(usage.get("cache_creation_input_tokens"), 0),
        "cost_usd": result_event.get("total_cost_usd"),
        "duration_ms": _as_int(result_event.get("duration_ms"), 0),
        "models_breakdown": None,
        "tool_call_count": tool_call_count,
    }


def _classify(classification: str, raw: str) -> dict[str, Any]:
    """Build payload for non-precise classifications using default back-off duration."""
    duration = _BACK_OFF_DEFAULTS[classification]
    return {
        "classification": classification,
        "agent": "claude",
        "reset_at_epoch": int(SYSTEM_CLOCK.epoch() + duration),
        "raw": raw,
    }


register_post_round_hook(ClaudeErrorDetector())
