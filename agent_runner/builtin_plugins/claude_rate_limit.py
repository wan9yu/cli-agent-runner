"""Built-in post_round_hook: detect claude rate-limit rejections in JSONL output.

Parses the round log tail for claude's `rate_limit_event` and emits
`rate_limit_rejected`. No-op for non-claude presets.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from agent_runner.events import RATE_LIMIT_REJECTED, emit
from agent_runner.hooks import HookContext, register_post_round_hook

_TAIL_LINES = 50
_RAW_CAP = 200


class ClaudeRateLimitDetector:
    """Parse claude round log tail for rate_limit_event; emit rate_limit_rejected."""

    name = "claude_rate_limit_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_name != "claude":
            return
        log_path = ctx.log_dir / f"round-{ctx.round_num}.log"
        if not log_path.exists():
            return
        payload = _scan_log_for_rate_limit(log_path)
        if payload is None:
            return
        payload["round_num"] = ctx.round_num
        emit(ctx.log_dir, RATE_LIMIT_REJECTED, **payload)


def _scan_log_for_rate_limit(log_path: Path) -> dict[str, Any] | None:
    """Scan last _TAIL_LINES of log_path for claude rate-limit signals.

    Returns dict suitable for rate_limit_rejected payload, or None if no signal.
    """
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=_TAIL_LINES)
    rate_limit_info: dict[str, Any] | None = None
    error_result: str | None = None
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            event.get("type") == "rate_limit_event"
            and event.get("rate_limit_info", {}).get("status") == "rejected"
        ):
            rate_limit_info = event["rate_limit_info"]
        elif (
            event.get("type") == "result"
            and event.get("is_error") is True
            and event.get("api_error_status") == 429
        ):
            error_result = str(event.get("result", ""))
    if rate_limit_info is None and error_result is None:
        return None
    if rate_limit_info is not None:
        reset_at = int(rate_limit_info.get("resetsAt", time.time() + 300))
        limit_type = str(rate_limit_info.get("rateLimitType", "unknown"))
    else:
        # 429 result without preceding rate_limit_event — defensive fallback
        reset_at = int(time.time() + 300)
        limit_type = "unknown"
    return {
        "agent": "claude",
        "reset_at_epoch": reset_at,
        "limit_type": limit_type,
        "raw": (error_result or "")[:_RAW_CAP],
    }


register_post_round_hook(ClaudeRateLimitDetector())
