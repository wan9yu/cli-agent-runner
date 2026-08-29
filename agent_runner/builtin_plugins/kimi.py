"""Built-in post_round_hook for kimi CLI: transient-error classification.

Fourth built-in plugin (after claude, gemini, codewhale). Parses the tail of a
`kimi --output-format stream-json -p ...` round log and emits
transient_error_detected so the supervisor can back off instead of hot-looping
through a provider outage.

Scope (verified against Kimi Code CLI 0.29.1, not documentation): the CLI's
stream-json writer emits exactly three record kinds during a prompt run --
`{"role":"assistant",...}`, `{"role":"tool",...}`, and `{"role":"meta",...}`
with type `session.resume_hint` (terminal) or `turn.step.retrying`. Only the
retry record carries provider failure data, and it is the shape this plugin
keys on:

    {"role":"meta","type":"turn.step.retrying","failed_attempt":1,
     "next_attempt":2,"max_attempts":10,"delay_ms":566.68,
     "error_name":"APIProviderRateLimitError",
     "error_message":"429 ...","status_code":429}

No usage events: the stream-json writer carries no token counters at all (no
usage/result record exists in that format), so this plugin emits no
agent_usage_recorded rather than publishing zeros that would read as a round
that cost nothing. Usage lands if a future CLI release surfaces counters in the
round output.

Fatal errors that kimi does not retry (auth, unknown model) never produce a
retry record -- they arrive as plain text on stderr, e.g.
``error: failed to run prompt: provider.auth_error: 401 Invalid Authentication``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent_runner.api import emit_transient_error_detected
from agent_runner.builtin_plugins._constants import (
    _BACK_OFF_DEFAULTS,
    _RAW_CAP,
    classify_transient_status,
    json_events,
)
from agent_runner.hooks import HookContext, register_post_round_hook


class KimiErrorDetector:
    """Parse kimi round log tail; emit transient_error_detected events."""

    name = "kimi_error_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_binary != "kimi":
            return
        log_path = ctx.agent_log_path
        if log_path is None or not log_path.exists():
            return
        # kimi retries provider failures itself (max_attempts 10 with growing
        # delays). A round that still succeeded absorbed the blip, so only a
        # failed round warrants supervisor-level back-off.
        if result.ok:
            return
        transient_error = _parse_kimi_log(log_path)
        if transient_error:
            emit_transient_error_detected(
                ctx.log_dir, round_num=ctx.round_num, phase=ctx.phase or "", **transient_error
            )


def _parse_kimi_log(log_path: Path) -> dict[str, Any] | None:
    """Scan the JSON tail window for the last `turn.step.retrying` record and
    map its status_code to a transient bucket. Returns None when nothing maps.

    kimi's fatal errors are plain text on stderr, so a round can legitimately
    reach here with no JSON record at all.
    """
    retry_event: dict | None = None
    for event in json_events(log_path):
        if event.get("role") == "meta" and event.get("type") == "turn.step.retrying":
            retry_event = event

    if retry_event is None:
        return None
    classification = classify_transient_status(retry_event.get("status_code"))
    if classification is None:
        return None
    return {
        "classification": classification,
        "agent": "kimi",
        "reset_at_epoch": int(time.time() + _BACK_OFF_DEFAULTS[classification]),
        "raw": str(retry_event.get("error_message", "retrying"))[:_RAW_CAP],
    }


register_post_round_hook(KimiErrorDetector())
