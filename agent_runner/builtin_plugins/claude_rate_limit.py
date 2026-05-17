"""Built-in post_round_hook: classify claude transient errors from JSONL output.

Classifies into 4 buckets (rate_limit_account / rate_limit_model /
api_transient_5xx / api_timeout) and emits transient_error_detected events
with computed reset_at_epoch. Supervisor consumes the event.

Also emits agent_usage_recorded per-round with token/cost data from the
claude result event (0.1.24+).

Naming history: was `claude_rate_limit_detector` in 0.1.20 (single-purpose
rate-limit detector). Renamed + generalized to multi-classification in 0.1.23.
Old plugin name `claude_rate_limit_detector` retained as entry-point alias
via pyproject.toml.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from agent_runner.api import (
    emit_agent_usage_recorded,
    emit_rate_limit_rejected,  # 0.1.20 alias, dual-emit during 0.1.23
    emit_transient_error_detected,
)
from agent_runner.builtin_plugins._constants import (
    _5XX_STATUSES,
    _BACK_OFF_DEFAULTS,
    _RAW_CAP,
    _TAIL_LINES,
)
from agent_runner.hooks import HookContext, register_post_round_hook


class ClaudeErrorDetector:
    """Classify claude transient errors; emit transient_error_detected + usage events."""

    name = "claude_error_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_name != "claude":
            return
        log_path = ctx.agent_log_path
        if log_path is None or not log_path.exists():
            return
        parsed = _parse_claude_log(log_path)

        if parsed.get("transient_error"):
            te = parsed["transient_error"]
            # Emit new generic event (all classifications)
            emit_transient_error_detected(ctx.log_dir, round_num=ctx.round_num, **te)
            # 0.1.23 BACK-COMPAT: also emit 0.1.20 event for rate_limit_account only
            if te["classification"] == "rate_limit_account":
                emit_rate_limit_rejected(
                    ctx.log_dir,
                    agent=te["agent"],
                    reset_at_epoch=te["reset_at_epoch"],
                    limit_type="five_hour",
                    round_num=ctx.round_num,
                    raw=te["raw"],
                )

        if parsed.get("usage"):
            emit_agent_usage_recorded(
                ctx.log_dir,
                round_num=ctx.round_num,
                phase=ctx.phase or "",
                success=(result.exit_code == 0 and not result.timed_out),
                **parsed["usage"],
            )


def _parse_claude_log(log_path: Path) -> dict[str, Any]:
    """Scan last _TAIL_LINES for rate_limit/result/assistant events.

    Returns dict with optional 'transient_error' and 'usage' keys.
    """
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=_TAIL_LINES)
    rate_limit_info: dict | None = None
    result_event: dict | None = None
    assistant_model: str | None = None
    tool_call_count = 0
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "rate_limit_event":
            rli = event.get("rate_limit_info", {})
            if rli.get("status") == "rejected":
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
                tool_call_count += sum(
                    1 for c in content if isinstance(c, dict) and c.get("type") == "tool_use"
                )

    out: dict[str, Any] = {}

    error_payload = _classify_transient_error(rate_limit_info, result_event)
    if error_payload is not None:
        out["transient_error"] = error_payload

    if result_event is not None:
        usage_payload = _extract_usage(
            result_event, model=assistant_model, tool_call_count=tool_call_count
        )
        if usage_payload is not None:
            out["usage"] = usage_payload

    return out


def _classify_transient_error(
    rate_limit_info: dict | None, result_event: dict | None
) -> dict | None:
    """Refactored from prior _scan_log_for_transient_error 0.1.23 logic; same shape, same
    priority (rate_limit_event.rejected > 429 > 5xx > 408).
    """
    if rate_limit_info is not None and rate_limit_info.get("rateLimitType") == "five_hour":
        return {
            "classification": "rate_limit_account",
            "agent": "claude",
            "reset_at_epoch": int(rate_limit_info.get("resetsAt", time.time() + 300)),
            "raw": str((result_event or {}).get("result", ""))[:_RAW_CAP],
        }
    # rate_limit_event with null/other rateLimitType falls through to status-based
    # classification below.
    if result_event is None or result_event.get("is_error") is not True:
        return None
    status = result_event.get("api_error_status")
    raw = str(result_event.get("result", ""))[:_RAW_CAP]
    if status == 429:
        return _classify("rate_limit_model", raw)
    if status in _5XX_STATUSES:
        return _classify("api_transient_5xx", raw)
    if status == 408:
        return _classify("api_timeout", raw)
    return None


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
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "cached_tokens": int(usage.get("cache_read_input_tokens", 0)),
        "cache_creation_tokens": int(usage.get("cache_creation_input_tokens", 0)),
        "cost_usd": result_event.get("total_cost_usd"),
        "duration_ms": int(result_event.get("duration_ms", 0)),
        "models_breakdown": None,
        "tool_call_count": tool_call_count,
    }


def _classify(classification: str, raw: str) -> dict[str, Any]:
    """Build payload for non-precise classifications using default back-off duration."""
    duration = _BACK_OFF_DEFAULTS[classification]
    return {
        "classification": classification,
        "agent": "claude",
        "reset_at_epoch": int(time.time() + duration),
        "raw": raw,
    }


register_post_round_hook(ClaudeErrorDetector())
