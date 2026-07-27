"""Built-in post_round_hook for pi CLI: usage events + transient classification.

Fifth built-in plugin (after claude, gemini, codewhale, kimi). Parses the tail
of a `pi -p -na --mode json ...` round log and emits both agent_usage_recorded
(pi's stream carries token counters) and transient_error_detected.

Scope (verified against Pi Coding Agent 0.80.10, not documentation):

**pi exits 0 on provider failure.** An invalid API key, and three exhausted
retries against 429/503, all produced exit code 0 with an empty stderr. The
exit code therefore cannot gate the transient emission the way it does for
kimi; the authoritative failure signal is the final assistant message's
``stopReason == "error"`` in the JSONL (supervisor-side ``timed_out`` /
non-zero exit still count as failure on top of it).

**Usage is per-message, not cumulative.** Each assistant message carries
``usage`` with ``input`` (net, i.e. prompt minus cached), ``output`` (reasoning
included), ``cacheRead``, ``cacheWrite``, ``totalTokens`` and a ``cost``
sub-object. A two-turn round reported 60/20/40 then 210/7/90 — reading only the
final message would drop every earlier turn — so the round total is the sum
across the round's assistant messages. The aggregation source is the
``agent_end`` records: each one is a single line listing exactly the messages
its agent run produced (disjoint across retries), which survives the tail
window even when thousands of ``message_update`` deltas precede it. When the
round was killed before any ``agent_end`` was written, the parser falls back to
summing the completed ``message_end`` records.

``message_update`` is never parsed: every delta repeats the full message state
with zeroed usage and a stale ``stopReason``, so treating one as terminal would
publish a phantom round.

Usage is emitted only when the round actually consumed tokens. Failed rounds
are not excluded — a round that spent tokens in earlier turns before failing is
a real cost record, emitted with ``success=False`` — but pi reports all-zero
usage for a call that never reached the model (auth failure, exhausted retries),
and publishing those zeros would read as a round that cost nothing.

``cost_usd`` comes from ``usage.cost.total``, which pi computes only when the
model catalog carries pricing; custom-provider entries have none and report 0,
reported as None rather than a fabricated $0.00.

Transient classification reads the failing message's ``errorMessage``. Two
formats were observed -- ``"429 status code (no body)"`` and
``"401: {...json body...}"`` -- both leading with the HTTP status, plus the
status-less ``"Request timed out."``. Auth (401) and unknown-model (404) map to
nothing: they are permanent until an operator intervenes, and the monitor's
default ``auth_fail_patterns`` already match pi's 401 wording in the raw log.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
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
    json_tail,
)
from agent_runner.hooks import HookContext, register_post_round_hook

_STATUS_RE = re.compile(r"^\s*(\d{3})\b")
"""pi prefixes provider errors with the HTTP status (``429 status code ...``,
``401: {...}``)."""


class PiErrorDetector:
    """Parse pi round log tail; emit usage + transient_error_detected events."""

    name = "pi_error_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_binary != "pi":
            return
        log_path = ctx.agent_log_path
        if log_path is None or not log_path.exists():
            return
        parsed = _parse_pi_log(log_path)
        messages = parsed["assistant_messages"]
        if not messages:
            return

        error_text = _final_error(messages, parsed["retry_final_error"])
        round_ok = result.exit_code == 0 and not result.timed_out and error_text is None

        if not round_ok:
            classification = _classify_pi_error(error_text)
            if classification:
                emit_transient_error_detected(
                    ctx.log_dir,
                    round_num=ctx.round_num,
                    classification=classification,
                    agent="pi",
                    reset_at_epoch=int(time.time() + _BACK_OFF_DEFAULTS[classification]),
                    raw=str(error_text)[:_RAW_CAP],
                )

        usage = _aggregate_usage(messages, parsed["session_start_ms"])
        if usage:
            emit_agent_usage_recorded(
                ctx.log_dir,
                round_num=ctx.round_num,
                phase=ctx.phase or "",
                success=round_ok,
                **usage,
            )


def _parse_pi_log(log_path: Path) -> dict[str, Any]:
    """Collect the round's assistant messages plus the failure signals.

    Prefers ``agent_end`` records (one line per agent run, listing only that
    run's messages) and falls back to ``message_end`` when the round was killed
    before any ``agent_end`` was written. Tolerates non-JSON lines: the round
    log merges stdout+stderr.
    """
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        tail = json_tail(f)
        f.seek(0)
        session_start_ms = _session_start_ms(f.readline())

    from_agent_end: list[dict] = []
    from_message_end: list[dict] = []
    retry_final_error: str | None = None
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
        if etype == "agent_end":
            from_agent_end.extend(_assistants(event.get("messages")))
        elif etype == "message_end":
            from_message_end.extend(_assistants([event.get("message")]))
        elif etype == "auto_retry_end" and not event.get("success"):
            retry_final_error = event.get("finalError")

    return {
        "assistant_messages": from_agent_end or from_message_end,
        "retry_final_error": retry_final_error,
        "session_start_ms": session_start_ms,
    }


def _assistants(messages: Any) -> list[dict]:
    """Assistant messages out of a pi message list (user/toolResult dropped)."""
    if not isinstance(messages, list):
        return []
    return [m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"]


def _session_start_ms(first_line: str) -> int | None:
    """Epoch-ms of the round's ``session`` header line, or None if absent."""
    try:
        event = json.loads(first_line)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "session":
        return None
    stamp = event.get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _final_error(messages: list[dict], retry_final_error: str | None) -> str | None:
    """Failure text of the round, or None when the round ended cleanly.

    The last assistant message is authoritative (post-retry state); the
    exhausted-retry record is the fallback for a stream that ends without one.
    """
    last = messages[-1]
    if last.get("stopReason") == "error":
        return str(last.get("errorMessage") or "error")
    if retry_final_error:
        return str(retry_final_error)
    return None


def _aggregate_usage(messages: list[dict], session_start_ms: int | None) -> dict[str, Any] | None:
    """Sum per-message usage into an agent_usage_recorded payload, or None.

    None when the round consumed no tokens at all (nothing reached the model),
    since a zeroed record would read as a round that cost nothing.
    """
    totals = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    cost = 0.0
    tool_calls = 0
    for message in messages:
        usage = message.get("usage")
        if isinstance(usage, dict):
            for key in totals:
                totals[key] += int(usage.get(key) or 0)
            message_cost = usage.get("cost")
            if isinstance(message_cost, dict):
                cost += float(message_cost.get("total") or 0)
        tool_calls += sum(
            1
            for block in message.get("content") or []
            if isinstance(block, dict) and block.get("type") == "toolCall"
        )
    if not any(totals.values()):
        return None

    last = messages[-1]
    provider, model = last.get("provider"), str(last.get("model") or "unknown")
    return {
        "agent": "pi",
        "model": f"{provider}/{model}" if provider else model,
        "input_tokens": totals["input"],
        "output_tokens": totals["output"],
        "cached_tokens": totals["cacheRead"],
        "cache_creation_tokens": totals["cacheWrite"],
        "cost_usd": cost if cost > 0 else None,
        "duration_ms": _duration_ms(last, session_start_ms),
        "models_breakdown": None,  # --model pins one model per round
        "tool_call_count": tool_calls,
    }


def _duration_ms(last_message: dict, session_start_ms: int | None) -> int:
    """Wall time from the session header to the final assistant message.

    Includes pi's own auto-retry back-off, which is real round latency. 0 when
    either endpoint is missing (e.g. the session header scrolled out of a
    truncated log).
    """
    end = last_message.get("timestamp")
    if session_start_ms is None or not isinstance(end, int):
        return 0
    return max(0, end - session_start_ms)


def _classify_pi_error(text: str | None) -> str | None:
    """Map a pi ``errorMessage`` to a transient bucket, or None.

    None means 'not a transient error': auth (401) and unknown model (404) are
    the non-transient failures observed from this CLI, both permanent until an
    operator fixes configuration, and both oauth_fail / config territory rather
    than something a back-off would clear.
    """
    if not text:
        return None
    match = _STATUS_RE.match(text)
    if match:
        status = int(match.group(1))
        if status == 429:
            return "rate_limit_model"
        if status in _5XX_STATUSES:
            return "api_transient_5xx"
        if status == 408:
            return "api_timeout"
        return None
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "api_timeout"
    return None


register_post_round_hook(PiErrorDetector())
