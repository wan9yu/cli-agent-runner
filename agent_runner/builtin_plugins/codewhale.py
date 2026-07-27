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

import time
from pathlib import Path
from typing import Any

from agent_runner.api import (
    emit_agent_usage_recorded,
    emit_transient_error_detected,
)
from agent_runner.builtin_plugins._constants import (
    _BACK_OFF_DEFAULTS,
    _RAW_CAP,
    classify_transient_status,
    json_events,
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
                success=result.ok,
                **parsed["usage"],
            )


def _parse_codewhale_log(log_path: Path) -> dict[str, Any]:
    """Scan the JSON tail window of codewhale NDJSON; extract usage from the metadata
    record; classify any {"type":"error"} that maps to a transient bucket.

    codewhale prefixes some stdout lines with terminal escapes, so the
    non-JSON lines ``json_events`` drops are routine here, not a corruption
    signal.
    """
    metadata: dict | None = None
    error_event: dict | None = None
    for event in json_events(log_path):
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
            "cost_usd": None,  # codewhale exec stdout exposes no USD
            "duration_ms": 0,  # not in exec metadata
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
    """Pull a status code out of a codewhale {"type":"error"} record for the
    shared ladder.

    The only error record captured so far carries a free-text 'error' string
    and no status code at all (an auth failure), so nothing maps today; the
    numeric-field lookup is the forward path for when a real rate-limit or 5xx
    sample is captured.
    """
    return classify_transient_status(error_event.get("code") or error_event.get("status_code"))


register_post_round_hook(CodewhaleErrorDetector())
