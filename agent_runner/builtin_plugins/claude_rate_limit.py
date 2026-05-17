"""Built-in post_round_hook: classify claude transient errors from JSONL output.

Classifies into 4 buckets (rate_limit_account / rate_limit_model /
api_transient_5xx / api_timeout) and emits transient_error_detected events
with computed reset_at_epoch. Supervisor consumes the event.

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
    emit_rate_limit_rejected,  # 0.1.20 alias, dual-emit during 0.1.23
    emit_transient_error_detected,
)
from agent_runner.hooks import HookContext, register_post_round_hook

_TAIL_LINES = 50
_RAW_CAP = 200

# Default back-off durations (seconds) for non-precise classifications.
# `rate_limit_account` uses claude's resetsAt epoch directly.
_BACK_OFF_DEFAULTS: dict[str, int] = {
    "rate_limit_model": 60,
    "api_transient_5xx": 60,
    "api_timeout": 30,
}

# claude 5xx codes treated as transient (retry-worthy server errors per RFC 9110):
# 500 = unexpected error, 502 = bad gateway, 503 = unavailable, 504 = gateway timeout.
# Excluded: 501 (not implemented = permanent), 505 (HTTP version mismatch = permanent).
_5XX_STATUSES: frozenset[int] = frozenset({500, 502, 503, 504})


class ClaudeErrorDetector:
    """Classify claude transient errors; emit transient_error_detected."""

    name = "claude_error_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_name != "claude":
            return
        log_path = ctx.log_dir / f"round-{ctx.round_num}.log"
        if not log_path.exists():
            return
        payload = _scan_log_for_transient_error(log_path)
        if payload is None:
            return
        payload["round_num"] = ctx.round_num

        # Emit new generic event (all classifications)
        emit_transient_error_detected(ctx.log_dir, **payload)

        # 0.1.23 BACK-COMPAT: also emit 0.1.20 event for rate_limit_account only
        if payload["classification"] == "rate_limit_account":
            emit_rate_limit_rejected(
                ctx.log_dir,
                agent=payload["agent"],
                reset_at_epoch=payload["reset_at_epoch"],
                limit_type="five_hour",
                round_num=payload["round_num"],
                raw=payload["raw"],
            )


def _scan_log_for_transient_error(log_path: Path) -> dict[str, Any] | None:
    """Scan last _TAIL_LINES; classify into one of 4 buckets or None.

    Priority (first match wins): rate_limit_event.rejected > 429 > 5xx > 408.
    Other api_error_status (403, 404, etc.) -> None (not transient; let supervisor proceed).
    """
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=_TAIL_LINES)
    rate_limit_info: dict | None = None
    result_event: dict | None = None
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "rate_limit_event":
            rli = event.get("rate_limit_info", {})
            if rli.get("status") == "rejected":
                rate_limit_info = rli
        elif event.get("type") == "result" and event.get("is_error") is True:
            result_event = event

    # Priority 1: rate_limit_event rejected -> rate_limit_account
    if rate_limit_info is not None:
        return {
            "classification": "rate_limit_account",
            "agent": "claude",
            "reset_at_epoch": int(rate_limit_info.get("resetsAt", time.time() + 300)),
            "raw": str((result_event or {}).get("result", ""))[:_RAW_CAP],
        }

    if result_event is None:
        return None

    status = result_event.get("api_error_status")
    raw = str(result_event.get("result", ""))[:_RAW_CAP]

    # Priority 2-4: api_error_status-based classifications
    if status == 429:
        return _classify("rate_limit_model", raw)
    if status in _5XX_STATUSES:
        return _classify("api_transient_5xx", raw)
    if status == 408:
        return _classify("api_timeout", raw)

    return None  # not a transient error we recognize


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
