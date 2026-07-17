"""Throttle state helpers — read events.jsonl tail for transient error state.

Internal module. Callers: runner.py (serve loop back-off), api.py (peek).
Separated from runner.py to satisfy the ouroboros defense: runner.py writes
events.jsonl but must never read it back (§3 module boundary invariant).
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from agent_runner.api_types import TransientErrorState
from agent_runner.events import TRANSIENT_ERROR_DETECTED, TRANSIENT_ERROR_RECOVERED


def _check_throttle_state(log_dir: Path) -> TransientErrorState | None:
    """Scan events.jsonl tail for latest unmatched transient error.

    Reads `transient_error_detected` / `transient_error_recovered` event names.
    Returns TransientErrorState if currently throttled (reset still in future,
    no matching recovered after). Restart-safe.
    """
    candidates = sorted(log_dir.glob("events-*.jsonl"))
    if not candidates:
        return None
    with candidates[-1].open() as f:
        tail = deque(f, maxlen=100)
    events: list[dict[str, Any]] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    latest_detected: dict[str, Any] | None = None
    for ev in reversed(events):
        kind = ev.get("event")
        if kind == TRANSIENT_ERROR_RECOVERED:
            return None
        if kind == TRANSIENT_ERROR_DETECTED:
            latest_detected = ev
            break

    if latest_detected is None:
        return None
    reset_at = int(latest_detected.get("reset_at_epoch", 0))
    if reset_at <= time.time():
        return None  # Reset already passed without recovery emit; treat as recovered

    classification = str(latest_detected.get("classification", "rate_limit_account"))

    return TransientErrorState(
        reset_at_epoch=reset_at,
        classification=classification,
        agent=str(latest_detected.get("agent", "unknown")),
        since_round=int(latest_detected.get("round_num", 0)),
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
    applied_reset_at = int(time.time()) + applied_duration

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
