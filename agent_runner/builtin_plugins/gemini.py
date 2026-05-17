"""Built-in post_round_hook for gemini CLI: usage events + transient error classifier.

Validates the 0.1.23 multi-CLI architecture: this is the second built-in
plugin (after claude) emitting the generic transient_error_detected and
agent_usage_recorded event families without any agent-runner core changes.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from agent_runner.api import (
    emit_agent_usage_recorded,
    emit_transient_error_detected,
)
from agent_runner.hooks import HookContext, register_post_round_hook

_TAIL_LINES = 50
_RAW_CAP = 200

# Default back-off durations for gemini transient classifications.
# Matches claude defaults (same 0.1.23 semantics).
_BACK_OFF_DEFAULTS: dict[str, int] = {
    "rate_limit_model": 60,
    "api_transient_5xx": 60,
    "api_timeout": 30,
}

# gemini 5xx codes treated as transient (retry-worthy server errors per RFC 9110):
# 500 = unexpected error, 502 = bad gateway, 503 = unavailable, 504 = gateway timeout.
# Excluded: 501 (not implemented = permanent), 505 (HTTP version mismatch = permanent).
_5XX_STATUSES: frozenset[int] = frozenset({500, 502, 503, 504})


class GeminiErrorDetector:
    """Parse gemini round log tail; emit usage + transient_error_detected events."""

    name = "gemini_error_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_name != "gemini":
            return
        log_path = ctx.log_dir / f"round-{ctx.round_num}.log"
        if not log_path.exists():
            return
        parsed = _parse_gemini_log(log_path)
        if parsed.get("transient_error"):
            te = parsed["transient_error"]
            te["round_num"] = ctx.round_num
            emit_transient_error_detected(ctx.log_dir, **te)
        if parsed.get("usage"):
            emit_agent_usage_recorded(ctx.log_dir, round_num=ctx.round_num, **parsed["usage"])


def _parse_gemini_log(log_path: Path) -> dict[str, Any]:
    """Scan last _TAIL_LINES; extract usage from result.stats; classify any error.

    Returns dict with optional 'usage' and 'transient_error' keys.
    """
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=_TAIL_LINES)
    result_event: dict | None = None
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result_event = event
    if result_event is None:
        return {}

    out: dict[str, Any] = {}

    # Usage extraction
    stats = result_event.get("stats") or {}
    if stats:
        out["usage"] = _extract_usage(stats)

    # Error classification (best-effort; gemini error schema less documented)
    if result_event.get("status") == "error" or result_event.get("error"):
        err = result_event.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        raw = str(
            (err.get("message") if isinstance(err, dict) else None)
            or result_event.get("status", "error")
        )[:_RAW_CAP]
        classification = _classify_gemini_error(code)
        if classification:
            duration = _BACK_OFF_DEFAULTS[classification]
            out["transient_error"] = {
                "classification": classification,
                "agent": "gemini",
                "reset_at_epoch": int(time.time() + duration),
                "raw": raw,
            }
    return out


def _extract_usage(stats: dict[str, Any]) -> dict[str, Any]:
    """Build agent_usage_recorded payload from gemini result.stats."""
    models = stats.get("models") or {}
    primary_model = (
        max(models, key=lambda m: models[m].get("total_tokens", 0)) if models else "unknown"
    )
    input_total = int(stats.get("input_tokens", 0))
    cached = int(stats.get("cached", 0))
    return {
        "agent": "gemini",
        "model": primary_model,
        "input_tokens": max(input_total - cached, 0),
        "output_tokens": int(stats.get("output_tokens", 0)),
        "cached_tokens": cached,
        "cost_usd": None,  # gemini doesn't expose USD
        "duration_ms": int(stats.get("duration_ms", 0)),
        "models_breakdown": models if len(models) > 1 else None,
    }


def _classify_gemini_error(code: Any) -> str | None:
    """Map gemini error code to 0.1.23 classification. None means 'not transient'."""
    if code == 429:
        return "rate_limit_model"
    if code in _5XX_STATUSES:
        return "api_transient_5xx"
    if code == 408:
        return "api_timeout"
    return None


register_post_round_hook(GeminiErrorDetector())
