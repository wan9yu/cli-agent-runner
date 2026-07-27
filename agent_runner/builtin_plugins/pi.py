"""Built-in post_round_hook for pi CLI: usage, transient, and auth events.

Fifth built-in plugin (after claude, gemini, codewhale, kimi). Parses the tail
of a `pi -p -na --mode json ...` round log and emits agent_usage_recorded (pi's
stream carries token counters), transient_error_detected, and
agent_auth_error_detected.

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
status-less ``"Request timed out."`` that only the text fallback catches.

A 401 gets no transient bucket (back-off cannot fix a rejected credential) and
instead emits ``agent_auth_error_detected``: because pi exits 0, the monitor's
text heuristic — which requires a nonzero exit — can never see a pi auth loop,
whereas that event is certain evidence the detector counts directly. 401 is the
only auth status observed from pi 0.80.10; 403 would qualify on the same
reasoning but has not been seen, and this parser stays on observed shapes.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any

from agent_runner.api import (
    emit_agent_auth_error_detected,
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

_STATUS_RE = re.compile(r"^\s*(\d{3})\b")
"""pi prefixes provider errors with the HTTP status (``429 status code ...``,
``401: {...}``)."""

_SESSION_HEAD_LINES: int = 20
"""Lines to scan for the ``session`` header before giving up on it.

Not just the first line: a real round often opens with plain-text node
warnings on stderr, and anchoring on line 1 would silently zero every duration.
"""


class PiErrorDetector:
    """Parse pi round log tail; emit usage + transient_error_detected events."""

    name = "pi_error_detector"

    def after_round(self, ctx: HookContext, result: Any) -> None:
        if ctx.agent_binary != "pi":
            return
        log_path = ctx.agent_log_path
        if log_path is None or not log_path.exists():
            return
        messages, session_start_ms = _parse_pi_log(log_path)
        if not messages:
            return

        # The last assistant message is the round's verdict: it carries the
        # post-retry state, and pi writes it before the auto_retry_end record.
        last = messages[-1]
        is_error = last.get("stopReason") == "error"
        error_text = str(last.get("errorMessage") or "error") if is_error else None
        round_ok = result.ok and error_text is None

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
            elif _is_auth_error(error_text):
                # No transient classification on purpose — an auth failure is
                # permanent until an operator intervenes, so backing off would
                # only stall. The event is what lets the monitor see the loop.
                emit_agent_auth_error_detected(
                    ctx.log_dir,
                    round_num=ctx.round_num,
                    agent="pi",
                    raw=str(error_text)[:_RAW_CAP],
                )

        usage = _aggregate_usage(messages, session_start_ms)
        if usage:
            emit_agent_usage_recorded(
                ctx.log_dir,
                round_num=ctx.round_num,
                phase=ctx.phase or "",
                success=round_ok,
                **usage,
            )


def _parse_pi_log(log_path: Path) -> tuple[list[dict], int | None]:
    """The round's assistant messages plus the session-start epoch-ms.

    Prefers ``agent_end`` records (one line per agent run, listing only that
    run's messages) and falls back to ``message_end`` when the round was killed
    before any ``agent_end`` was written.
    """
    from_agent_end: list[dict] = []
    from_message_end: list[dict] = []
    for event in json_events(log_path):
        etype = event.get("type")
        if etype == "agent_end":
            from_agent_end.extend(_assistants(event.get("messages")))
        elif etype == "message_end":
            from_message_end.extend(_assistants([event.get("message")]))

    return from_agent_end or from_message_end, _session_start_ms(log_path)


def _assistants(messages: Any) -> list[dict]:
    """Assistant messages out of a pi message list (user/toolResult dropped)."""
    if not isinstance(messages, list):
        return []
    return [m for m in messages if isinstance(m, dict) and m.get("role") == "assistant"]


def _session_start_ms(log_path: Path) -> int | None:
    """Epoch-ms of the round's ``session`` header, or None if absent.

    Read from the head of the file, not the tail window: the header is the
    round's first record and would otherwise be evicted by a long round.
    """
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        head = list(islice(f, _SESSION_HEAD_LINES))
    for line in head:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "session":
            continue
        stamp = event.get("timestamp")
        if not isinstance(stamp, str):
            return None
        try:
            return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
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
    """Map a pi ``errorMessage`` to a transient bucket via the shared ladder.

    pi leads provider errors with the HTTP status, so the status prefix is
    authoritative when present. Only when there is none does the text fallback
    apply — pi's own ``"Request timed out."`` carries no status at all.
    """
    if not text:
        return None
    match = _STATUS_RE.match(text)
    if match:
        return classify_transient_status(int(match.group(1)))
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "api_timeout"
    return None


def _is_auth_error(text: str | None) -> bool:
    """True when pi's ``errorMessage`` carries an HTTP 401.

    Read from the same status prefix the transient classifier uses. 401 is the
    only auth status observed from pi 0.80.10 (invalid key against a Moonshot
    provider); 403 would qualify on the same reasoning but has not been seen,
    and this parser stays on shapes captured from a real run.
    """
    match = _STATUS_RE.match(text or "")
    return bool(match) and match.group(1) == "401"


register_post_round_hook(PiErrorDetector())
