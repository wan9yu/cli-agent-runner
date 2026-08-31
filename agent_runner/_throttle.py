"""Throttle state helpers — read events.jsonl tail for transient error state,
and own the supervisor-side back-off sleep.

Internal module. Callers: cli/serve_cmd.py (throttle scan + back-off + restart
delay), api.py (peek). The back-off lives here, NOT in runner.py, to satisfy the
ouroboros defense: runner.py writes events.jsonl but must never read it back
(§3 module boundary invariant), and back-off is driven by the events-derived
throttle state this module scans.
"""

from __future__ import annotations

import json
import math
import random
import warnings
from datetime import UTC
from pathlib import Path
from typing import Any

from agent_runner.api_types import TransientErrorState
from agent_runner.clock import SYSTEM_CLOCK, Clock
from agent_runner.events import (
    TRANSIENT_ERROR_DETECTED,
    TRANSIENT_ERROR_RECOVERED,
    parse_iso_ms,
)

# _scan_events_for_transient sentinel: file held no transient event at all
# (distinct from "latest transient was a recovered" → None).
_NO_TRANSIENT = object()


def _coerce_int(value: Any, default: int) -> int:
    """Coerce a parsed-event field to int, falling back to ``default`` on None or a
    non-numeric value with a ``UserWarning``. A plugin-written ``reset_at_epoch: null``
    must degrade to 'no throttle', never raise a TypeError out of the serve loop.

    NaN/Infinity are non-numeric for this purpose too: ``json.loads`` accepts the bare
    ``NaN``/``Infinity``/``-Infinity`` tokens by default, and ``int()`` on such a float
    raises (``ValueError`` for NaN, ``OverflowError`` for +-Infinity) — the same
    serve-loop crash this helper exists to prevent, just reached via a different
    malformed value than ``None``."""
    if isinstance(value, bool):
        value = int(value)  # bool is an int subclass; normalize before the isinstance below
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    warnings.warn(
        f"cannot coerce event value {value!r} to int; using default {default}", stacklevel=2
    )
    return default


def _coerce_float(value: Any, default: float) -> float:
    """Float sibling of ``_coerce_int`` for parsed-event metric fields.

    Rejects NaN/Infinity the same way ``_coerce_int`` does — a nonsensical
    ``disk_used_pct: NaN`` must not reach ``SystemMetrics`` as a silently-passed
    float; it degrades to ``default`` + a ``UserWarning`` instead."""
    if isinstance(value, bool):
        value = float(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            pass
        else:
            if math.isfinite(parsed):
                return parsed
    warnings.warn(
        f"cannot coerce event value {value!r} to float; using default {default}", stacklevel=2
    )
    return default


def _iter_events(path: Path):
    """Yield parsed event dicts from a JSONL file; skip blank / corrupt lines."""
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _scan_events_for_transient(path: Path):
    """The file's LAST transient event: the detected dict, ``None`` (latest was a
    recovered), or ``_NO_TRANSIENT`` (no transient event in the file).

    Forward single-pass, O(1) memory — it keeps only the latest transient event,
    never a 100-line tail. That matters for throttle-aware skip: the loop keeps
    emitting rounds on healthy phases during a throttle, so the ``detected`` event
    can be thousands of lines back; a bounded tail would scroll it out and make the
    supervisor forget the throttle mid-window."""
    latest: Any = _NO_TRANSIENT
    for ev in _iter_events(path):
        kind = ev.get("event")
        if kind == TRANSIENT_ERROR_DETECTED:
            latest = ev
        elif kind == TRANSIENT_ERROR_RECOVERED:
            latest = None
    return latest


def _latest_unrecovered_detected(log_dir: Path) -> dict[str, Any] | None:
    """The most recent ``transient_error_detected`` with no
    ``transient_error_recovered`` after it, or None. Used by
    :func:`_check_throttle_state` (still-throttled?) — the single-throttle "latest"
    view kept for the non-skip (wait / back_off / stop) paths and peek.

    Scans the newest monthly ``events-*.jsonl`` and, only if it holds no transient
    event yet, the previous month's — so a throttle that spans a month boundary
    (detected in the old file, cleared in the new) is not orphaned."""
    candidates = sorted(log_dir.glob("events-*.jsonl"))
    for path in reversed(candidates[-2:]):
        result = _scan_events_for_transient(path)
        if result is not _NO_TRANSIENT:
            return result  # a detected dict, or None (latest transient was recovered)
    return None


def _latest_transient_per_agent(log_dir: Path) -> dict[str, Any]:
    """Per agent label, the last transient event across the newest TWO monthly files:
    the ``detected`` dict, or ``None`` if the latest was a ``recovered``.

    Unlike :func:`_latest_unrecovered_detected` (which early-exits at the first file
    holding any transient), this always merges BOTH files per agent — a throttle for
    agent A in the old file must not be masked by agent B's event in the newer one.
    Iterates old-then-new so the newest event per agent wins."""
    latest: dict[str, Any] = {}
    for path in sorted(log_dir.glob("events-*.jsonl"))[-2:]:
        for ev in _iter_events(path):
            kind = ev.get("event")
            if kind == TRANSIENT_ERROR_DETECTED:
                latest[str(ev.get("agent", "unknown"))] = ev
            elif kind == TRANSIENT_ERROR_RECOVERED:
                latest[str(ev.get("agent", "unknown"))] = None
    return latest


def _active_throttles(
    log_dir: Path, *, clock: Clock = SYSTEM_CLOCK
) -> dict[str, TransientErrorState]:
    """Currently-active throttles keyed by AGENT label (binary basename), one entry
    per agent whose latest transient event is an unrecovered ``detected`` with its
    reset still in the future.

    Rate limits are per-provider (per API key / agent binary), not per-phase — so the
    serve skip-policy routes around EVERY phase sharing a throttled agent while a
    healthy agent's phase keeps running. Restart-safe (events-derived)."""
    now = clock.epoch()
    active: dict[str, TransientErrorState] = {}
    for agent, detected in _latest_transient_per_agent(log_dir).items():
        if detected is None:
            continue
        reset_at = _coerce_int(detected.get("reset_at_epoch"), 0)
        if reset_at <= now:
            continue
        active[agent] = TransientErrorState(
            reset_at_epoch=reset_at,
            classification=str(detected.get("classification", "rate_limit_account")),
            agent=agent,
            since_round=_coerce_int(detected.get("round_num"), 0),
            phase=str(detected.get("phase", "")),
        )
    return active


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
    reset_at = _coerce_int(latest_detected.get("reset_at_epoch"), 0)
    if reset_at <= now:
        return None  # Reset already passed without recovery emit; treat as recovered

    classification = str(latest_detected.get("classification", "rate_limit_account"))

    return TransientErrorState(
        reset_at_epoch=reset_at,
        classification=classification,
        agent=str(latest_detected.get("agent", "unknown")),
        since_round=_coerce_int(latest_detected.get("round_num"), 0),
        phase=str(latest_detected.get("phase", "")),
    )


def _throttled_for_s(ts: Any, *, clock: Clock = SYSTEM_CLOCK) -> int:
    """Seconds since a detected event's ``ts`` (best-effort; 0 if unparseable).
    ``clock`` supplies now; inject a ``FakeClock`` for exact-value tests."""
    if not ts:
        return 0
    try:
        detected = parse_iso_ms(str(ts))  # events.py owns the Z→+00:00 workaround
    except (ValueError, TypeError):
        return 0
    if detected.tzinfo is None:
        detected = detected.replace(tzinfo=UTC)  # hand-injected naive ts → treat as UTC
    return max(0, int(clock.epoch() - detected.timestamp()))


def pending_recovered(log_dir: Path, *, clock: Clock = SYSTEM_CLOCK) -> list[tuple[str, str, int]]:
    """One ``(agent, classification, throttled_for_s)`` per agent whose throttle
    cleared WITHOUT a recovered breadcrumb — its latest transient event is a
    ``detected`` with ``reset_at <= now`` and NO ``transient_error_recovered`` after.

    This is the throttle-aware-skip path: the loop rotated to a healthy phase instead
    of calling ``_apply_back_off`` (which emits its own recovered), so a multi-hour
    throttle would otherwise close with no breadcrumb. Events-derived → restart-safe
    AND never double-emits: once the caller emits recovered, that event sits after the
    detected one and the agent drops out. Per-agent, so an agent that cleared is
    reported even while a sibling is still throttled (overlapping recovery); an empty
    list means nothing cleared (all still throttled, or the back-off path already left
    a recovered)."""
    now = clock.epoch()
    cleared: list[tuple[str, str, int]] = []
    for agent, detected in _latest_transient_per_agent(log_dir).items():
        if detected is None or _coerce_int(detected.get("reset_at_epoch"), 0) > now:
            continue  # None = latest was a recovered; > now = still throttled
        cleared.append(
            (
                agent,
                str(detected.get("classification", "rate_limit_account")),
                _throttled_for_s(detected.get("ts"), clock=clock),
            )
        )
    return cleared


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


_BACK_OFF_CAP_S = 28800  # 8h — defensive cap; 1.6× the 5h-window
_BACK_OFF_JITTER_MIN_S = 5
_BACK_OFF_JITTER_MAX_S = 30


def _interruptible_sleep(
    total_s: float, stop: dict[str, bool], *, clock: Clock = SYSTEM_CLOCK, chunk_s: int = 30
) -> bool:
    """Sleep ``total_s`` in ``<= chunk_s`` slices, re-checking ``stop`` at each
    boundary; return True iff ``stop["requested"]`` cut it short. Shared by the serve
    restart delay and :func:`_apply_back_off` so a SIGTERM lands within one chunk.

    Counts down the *intended* nap per slice rather than measuring a wall/monotonic
    deadline: NTP-step immune (no clock read for the deadline) AND does not busy-spin
    when ``clock.sleep`` is a no-op (a test patching ``time.sleep`` runs it instantly
    instead of looping until real time advances)."""
    remaining = float(total_s)
    while remaining > 0:
        if stop["requested"]:
            return True
        nap = min(float(chunk_s), remaining)
        clock.sleep(nap)
        remaining -= nap
    return False


def _apply_back_off(
    log_dir: Path,
    throttle: TransientErrorState,
    *,
    stop: dict[str, bool],
    clock: Clock = SYSTEM_CLOCK,
    chunk_s: int = 30,
) -> bool:
    """Sleep until adjusted reset_at + jitter, then emit recovered. Returns True iff
    ``stop["requested"]`` was set before the sleep completed — an interrupted back-off,
    where the caller must break WITHOUT treating the throttle as recovered: the reset
    has not passed, so a recovered breadcrumb would poison restart-safe state.

    The sleep is chunked and re-checks ``stop`` at each boundary (see
    :func:`_interruptible_sleep`), so a SIGTERM lands within one chunk instead of after
    a multi-hour window.

    For estimated-class classifications (rate_limit_model / api_transient_5xx /
    api_timeout), applies exp backoff on consecutive failures via
    :func:`compute_adjusted_reset_at`. For server-authoritative rate_limit_account, the
    original reset_at_epoch is used verbatim. The 8h cap is a last-line defense against a
    malformed (far-future) reset epoch (e.g. a manual event with a far-future ts).
    """
    from agent_runner._emit import (
        emit_transient_error_backoff_capped,
        emit_transient_error_recovered,
    )

    adjusted_reset_at, _consecutive_count, _capped = compute_adjusted_reset_at(
        classification=throttle.classification,
        original_reset_at_epoch=throttle.reset_at_epoch,
        agent=throttle.agent,
        log_dir=log_dir,
        clock=clock,
    )

    requested = (
        adjusted_reset_at
        - clock.epoch()
        + random.uniform(_BACK_OFF_JITTER_MIN_S, _BACK_OFF_JITTER_MAX_S)
    )
    if requested > _BACK_OFF_CAP_S:
        # Defensive: malformed reset epoch. The exp-backoff layer caps at 30min, so
        # legitimate flow never reaches this branch.
        emit_transient_error_backoff_capped(
            log_dir,
            classification=throttle.classification,
            agent=throttle.agent,
            requested_sleep_s=int(requested),
            applied_sleep_s=_BACK_OFF_CAP_S,
        )
        sleep_s = float(_BACK_OFF_CAP_S)
    else:
        sleep_s = max(requested, 0.0)

    sleep_start = clock.epoch()
    if _interruptible_sleep(sleep_s, stop, clock=clock, chunk_s=chunk_s):
        return True  # SIGTERM during back-off — leave the throttle active, no breadcrumb

    emit_transient_error_recovered(
        log_dir,
        classification=throttle.classification,
        agent=throttle.agent,
        throttled_for_s=int(clock.epoch() - sleep_start),
    )
    return False
