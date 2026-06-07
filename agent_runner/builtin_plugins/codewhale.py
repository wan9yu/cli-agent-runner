"""Built-in post_round_hook for codewhale CLI: usage events + transient classifier.

Third built-in plugin (after claude, gemini). Parses codewhale's `exec
--output-format stream-json` NDJSON stdout tail; emits agent_usage_recorded
from the terminal metadata record. Transient-error classification is
best-effort and emits ONLY when an error maps to an existing bucket (like
gemini): codewhale's exec stdout surfaces a {"type":"error"} record, but the
only observed case so far is auth failure (oauth_fail territory, not a
transient bucket), so nothing maps yet -- usage-only today. 429/5xx mapping
is added when a real rate-limit sample is captured.
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
from agent_runner.builtin_plugins._constants import (
    _5XX_STATUSES,
    _BACK_OFF_DEFAULTS,
    _RAW_CAP,
    _TAIL_LINES,
)
from agent_runner.hooks import HookContext, register_post_round_hook


class CodewhaleErrorDetector:
    """Parse codewhale round log tail; emit usage + transient_error_detected events."""

    name = "codewhale_error_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_binary != "codewhale":
            return
        log_path = ctx.agent_log_path
        if log_path is None or not log_path.exists():
            return
        parsed = _parse_codewhale_log(log_path)
        if parsed.get("transient_error"):
            emit_transient_error_detected(
                ctx.log_dir, round_num=ctx.round_num, **parsed["transient_error"]
            )
        if parsed.get("usage"):
            emit_agent_usage_recorded(
                ctx.log_dir,
                round_num=ctx.round_num,
                phase=ctx.phase or "",
                success=(result.exit_code == 0 and not result.timed_out),
                **parsed["usage"],
            )


def _parse_codewhale_log(log_path: Path) -> dict[str, Any]:
    """Scan last _TAIL_LINES of codewhale NDJSON; extract usage from the metadata
    record; classify any {"type":"error"} that maps to a transient bucket.

    Tolerates non-JSON lines (codewhale prefixes some stdout with terminal
    escapes) via per-line try/except.
    """
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=_TAIL_LINES)
    metadata: dict | None = None
    error_event: dict | None = None
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if etype == "metadata":
            metadata = event.get("meta") or {}
        elif etype == "error":
            error_event = event

    out: dict[str, Any] = {}

    if metadata:
        out["usage"] = {
            "agent": "codewhale",
            "model": str(metadata.get("model", "unknown")),
            "input_tokens": int(metadata.get("input_tokens", 0)),
            "output_tokens": int(metadata.get("output_tokens", 0)),
            "cached_tokens": 0,  # codewhale exec stdout exposes no cache counts
            "cache_creation_tokens": 0,
            "cost_usd": None,  # codewhale exec stdout exposes no USD
            "duration_ms": 0,  # not in exec metadata
            "models_breakdown": None,
            "tool_call_count": 0,
        }

    if error_event is not None:
        classification = _classify_codewhale_error(error_event)
        if classification:
            duration = _BACK_OFF_DEFAULTS[classification]
            out["transient_error"] = {
                "classification": classification,
                "agent": "codewhale",
                "reset_at_epoch": int(time.time() + duration),
                "raw": str(error_event.get("error", "error"))[:_RAW_CAP],
            }
    return out


def _classify_codewhale_error(error_event: dict[str, Any]) -> str | None:
    """Map a codewhale {"type":"error"} record to a transient bucket, or None.

    None means 'not a transient error' (e.g. auth failure -> handled by the
    monitor's oauth_fail log-scan, not the transient classifier). codewhale's
    error record currently carries only a free-text 'error' string with no
    status code; until a real rate-limit/5xx sample is captured we cannot map
    to rate_limit_model / api_transient_5xx / api_timeout, so we return None.
    A future revision keys on a numeric status field once observed.
    """
    code = error_event.get("code") or error_event.get("status_code")
    if code == 429:
        return "rate_limit_model"
    if code in _5XX_STATUSES:
        return "api_transient_5xx"
    if code == 408:
        return "api_timeout"
    return None


register_post_round_hook(CodewhaleErrorDetector())
