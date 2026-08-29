"""Throttle state helpers — read events.jsonl tail for transient error state.

Internal module. Callers: runner.py (serve loop back-off), api.py (peek).
Separated from runner.py to satisfy the ouroboros defense: runner.py writes
events.jsonl but must never read it back (§3 module boundary invariant).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runner.api_types import TransientErrorState
from agent_runner.clock import SYSTEM_CLOCK, Clock
from agent_runner.events import TRANSIENT_ERROR_DETECTED, TRANSIENT_ERROR_RECOVERED

# _scan_events_for_transient sentinel: file held no transient event at all
# (distinct from "latest transient was a recovered" → None).
_NO_TRANSIENT = object()


def _scan_events_for_transient(path: Path):
    """The file's LAST transient event: the detected dict, ``None`` (latest was a
    recovered), or ``_NO_TRANSIENT`` (no transient event in the file).

    Forward single-pass, O(1) memory — it keeps only the latest transient event,
    never a 100-line tail. That matters for throttle-aware skip: the loop keeps
    emitting rounds on healthy phases during a throttle, so the ``detected`` event
    can be thousands of lines back; a bounded tail would scroll it out and make the
    supervisor forget the throttle mid-window."""
    latest: Any = _NO_TRANSIENT
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("event")
            if kind == TRANSIENT_ERROR_DETECTED:
                latest = ev
            elif kind == TRANSIENT_ERROR_RECOVERED:
                latest = None
    return latest


def _latest_unrecovered_detected(log_dir: Path) -> dict[str, Any] | None:
    """The most recent ``transient_error_detected`` with no
    ``transient_error_recovered`` after it, or None. Shared by
    :func:`_check_throttle_state` (still-throttled?) and :func:`pending_recovered`
    (cleared-without-a-breadcrumb?).

    Scans the newest monthly ``events-*.jsonl`` and, only if it holds no transient
    event yet, the previous month's — so a throttle that spans a month boundary
    (detected in the old file, cleared in the new) is not orphaned."""
    candidates = sorted(log_dir.glob("events-*.jsonl"))
    for path in reversed(candidates[-2:]):
        result = _scan_events_for_transient(path)
        if result is not _NO_TRANSIENT:
            return result  # a detected dict, or None (latest transient was recovered)
    return None


def _check_throttle_state(
    log_dir: Path, *, clock: Clock = SYSTEM_CLOCK
) -> TransientErrorState | None:
    """Scan events.jsonl tail for latest unmatched transient error.

    Reads `transient_error_detected` / `transient_error_recovered` event names.
    Returns TransientErrorState if currently throttled (reset still in future,
    no matching recovered after). Restart-safe.

    ``clock`` supplies the wall clock the reset is compared against — inject a
    ``FakeClock`` in tests to pin an exact instant. Only the reset comparison
    reads it; the event scan is pure.
    """
    now = clock.epoch()
    latest_detected = _latest_unrecovered_detected(log_dir)
    if latest_detected is None:
        return None
    reset_at = int(latest_detected.get("reset_at_epoch", 0))
    if reset_at <= now:
        return None  # Reset already passed without recovery emit; treat as recovered

    classification = str(latest_detected.get("classification", "rate_limit_account"))

    return TransientErrorState(
        reset_at_epoch=reset_at,
        classification=classification,
        agent=str(latest_detected.get("agent", "unknown")),
        since_round=int(latest_detected.get("round_num", 0)),
        phase=str(latest_detected.get("phase", "")),
    )


def _throttled_for_s(ts: Any, *, clock: Clock = SYSTEM_CLOCK) -> int:
    """Seconds since a detected event's ``ts`` (best-effort; 0 if unparseable).
    ``clock`` supplies now; inject a ``FakeClock`` for exact-value tests."""
    now = clock.epoch()
    if not ts:
        return 0
    try:
        detected = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0
    if detected.tzinfo is None:
        detected = detected.replace(tzinfo=UTC)  # events are UTC; don't read as local
    return max(0, int(now - detected.timestamp()))


def pending_recovered(log_dir: Path, *, clock: Clock = SYSTEM_CLOCK) -> tuple[str, str, int] | None:
    """``(classification, agent, throttled_for_s)`` iff a transient throttle
    cleared WITHOUT a recovered breadcrumb — the latest
    ``transient_error_detected`` has ``reset_at <= now`` and NO
    ``transient_error_recovered`` after it.

    This is the throttle-aware-skip path: the loop rotated to a healthy phase
    instead of calling ``_apply_back_off`` (which emits its own recovered), so
    the multi-hour throttle would otherwise close with no breadcrumb. Being
    events-derived, it is restart-safe AND never double-emits — once the caller
    emits recovered, that event sits after the detected one and the predicate
    goes quiet. Returns None while still throttled (reset in the future) or when
    the back-off path already left a recovered."""
    now = clock.epoch()
    latest_detected = _latest_unrecovered_detected(log_dir)
    if latest_detected is None:
        return None
    if int(latest_detected.get("reset_at_epoch", 0)) > now:
        return None  # still throttled — not a clear transition
    return (
        str(latest_detected.get("classification", "rate_limit_account")),
        str(latest_detected.get("agent", "unknown")),
        _throttled_for_s(latest_detected.get("ts"), clock=clock),
    )


# Module-level supervisor state — bucket → consecutive-failure count.
# Cleared by reset_counters() or by serve restart.
_consecutive_failures: dict[str, int] = {}


def compute_adjusted_reset_at(
    *,
    classification: str,
    original_reset_at_epoch: int,
    agent: str,
    log_dir: Path,
    clock: Clock = SYSTEM_CLOCK,
) -> tuple[int, int, bool]:
    """Apply exp backoff for estimated-class transient errors.

    Returns (applied_reset_at_epoch, consecutive_count, capped_by_absolute_max).

    For server-authoritative classification (``rate_limit_account``): returns
    the original reset epoch verbatim, never increments the counter, and
    never emits an adjustment event. Anthropic's resetsAt is authoritative.

    For any classification absent from ``_BACK_OFF_DEFAULTS`` — i.e. one a
    third-party plugin defined itself, which ``api_types.py`` types
    ``classification`` as ``str`` to permit — the same verbatim path applies:
    core has no base duration for it, so the emitter's reset_at_epoch is the
    only non-invented answer.

    For estimated classifications (``rate_limit_model``, ``api_transient_5xx``,
    ``api_timeout``): increments the counter for this bucket, computes
    duration = base × 2^min(n, _EXP_CAP), caps at _ABSOLUTE_CAP_S, emits
    ``transient_error_backoff_capped`` if multiplier > 1 or capped.
    """
    from agent_runner._emit import emit_transient_error_backoff_capped
    from agent_runner.builtin_plugins._constants import (
        _ABSOLUTE_CAP_S,
        _BACK_OFF_DEFAULTS,
        _EXP_CAP,
    )

    if classification == "rate_limit_account" or classification not in _BACK_OFF_DEFAULTS:
        # Server-authoritative, or a plugin's own classification (api_types.py types
        # `classification` as str precisely so plugins can add their own): the emitter
        # supplied reset_at_epoch, so respect it verbatim and never touch the counter.
        return (original_reset_at_epoch, 0, False)

    # Estimated class: apply exp backoff.
    base = _BACK_OFF_DEFAULTS[classification]
    n = _consecutive_failures.get(classification, 0)
    multiplier = 2 ** min(n, _EXP_CAP)
    extended_duration = base * multiplier
    capped_by_absolute_max = extended_duration > _ABSOLUTE_CAP_S
    applied_duration = min(extended_duration, _ABSOLUTE_CAP_S)
    applied_reset_at = int(clock.epoch()) + applied_duration

    new_count = n + 1
    _consecutive_failures[classification] = new_count

    # Emit observability event when supervisor adjusted the wait.
    if multiplier > 1 or capped_by_absolute_max:
        emit_transient_error_backoff_capped(
            log_dir,
            classification=classification,
            agent=agent,
            requested_sleep_s=int(base),
            applied_sleep_s=applied_duration,
            original_reset_at_epoch=original_reset_at_epoch,
            applied_reset_at_epoch=applied_reset_at,
            consecutive_count=new_count,
            capped_by_absolute_max=capped_by_absolute_max,
        )

    return (applied_reset_at, new_count, capped_by_absolute_max)


def reset_counters() -> None:
    """Clear all bucket counters. Called by serve loop when no active throttle."""
    _consecutive_failures.clear()
