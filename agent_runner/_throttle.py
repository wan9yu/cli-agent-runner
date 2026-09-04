"""Throttle state helpers — read events.jsonl tail for transient error state,
and own the supervisor-side back-off sleep.

Internal module. Callers: cli/serve_cmd.py (throttle scan + back-off + restart
delay), api.py (peek). The back-off lives here, NOT in runner.py, to satisfy the
ouroboros defense: runner.py writes events.jsonl but must never read it back
(§3 module boundary invariant), and back-off is driven by the events-derived
throttle state this module scans.
"""

from __future__ import annotations

import math
import random
import warnings
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import Any

from agent_runner.api_types import TransientErrorState
from agent_runner.clock import SYSTEM_CLOCK, Clock
from agent_runner.events import (
    AGENT_USAGE_RECORDED,
    MEM_LOOP,
    ROUND_MEM_TERMINATED,
    ROUND_SUBSTRATE_BEFORE,
    TRANSIENT_ERROR_DETECTED,
    TRANSIENT_ERROR_RECOVERED,
    iter_event_dicts,
    parse_iso_ms,
)

__all__ = [
    "_active_throttles",
    "_apply_back_off",
    "_check_throttle_state",
    "_interruptible_sleep",
    "compute_adjusted_reset_at",
    "effective_throttle_view",
    "mem_loop_events_in_window",
    "pending_recovered",
    "round_was_mem_terminated",
]

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
    """Yield parsed event dicts from a JSONL file; skip blank / corrupt lines
    and any line that decodes to something other than an object (a bare
    number/string/list) -- every caller assumes the shape ``ev.get(...)``."""
    return iter_event_dicts(path)


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
    view kept for the non-skip (back_off / skip / stop) paths and peek.

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


def _backoff_exponent(log_dir: Path, agent: str) -> int:
    """Events-derived exp-backoff exponent for ``agent``: the count of
    ``transient_error_detected`` events attributed to it MINUS ONE (first
    detection → 0, no multiplier), since that agent's last successful completed
    round (``agent_usage_recorded`` with truthy ``success``).

    Single-sourced and restart-safe: recomputed from the persisted stream on
    every read, so a restart mid-outage resolves the SAME n rather than
    double-applying a lost module counter. ``transient_error_recovered`` is
    deliberately NOT a reset point — the throttle-aware-skip path emits one every
    cycle, which would pin the exponent at <=1 and flatten the ladder."""
    count = 0
    for path in sorted(log_dir.glob("events-*.jsonl"))[-2:]:
        for ev in _iter_events(path):
            if str(ev.get("agent", "")) != agent:
                continue
            kind = ev.get("event")
            if kind == TRANSIENT_ERROR_DETECTED:
                count += 1
            elif kind == AGENT_USAGE_RECORDED and ev.get("success"):
                count = 0
    return max(0, count - 1)


def _extend_reset(classification: str, reset_at_epoch: int, exponent: int) -> int:
    """Anchor-stable exp-backoff extension for the events-derived effective view.

    Estimated classes (base in ``_BACK_OFF_DEFAULTS``) push the emitter's reset
    out by ``base * (2**min(exponent, _EXP_CAP) - 1)``, capped at
    ``_ABSOLUTE_CAP_S``; server-authoritative ``rate_limit_account`` and
    plugin-defined classes pass through verbatim. Anchored to ``reset_at_epoch``
    (NOT ``now``) so repeated scans — and a post-restart scan — yield the
    identical value; the skip loop reads it every cycle and must not drift."""
    from agent_runner.builtin_plugins._constants import (
        _ABSOLUTE_CAP_S,
        _BACK_OFF_DEFAULTS,
        _EXP_CAP,
    )

    base = _BACK_OFF_DEFAULTS.get(classification)
    if base is None or classification == "rate_limit_account" or exponent <= 0:
        return reset_at_epoch
    return reset_at_epoch + min(base * (2 ** min(exponent, _EXP_CAP) - 1), _ABSOLUTE_CAP_S)


def _events_derived_reset(
    log_dir: Path,
    agent: str,
    classification: str,
    reset_at_epoch: int,
    *,
    _exponent_cache: dict[str, int] | None = None,
) -> int:
    """The ONE ladder-extended reset every throttle reader converges on:
    :func:`_active_throttles` (skip path / crash-loop excuse via its map),
    :func:`_check_throttle_state` (serve's loop-top non-skip gate — this global-latest
    scalar used to compare the emitter's RAW ``reset_at_epoch`` instead, so a
    non-default ``restart_delay_s`` sleeping past the raw reset between rounds could
    make the gate wave a round through with no back-off while the ladder was still
    active), and ``monitor.detect_rate_limit_active`` (via its optional ``log_dir``).
    One function so the three can never compute this differently again.

    ``_exponent_cache``, when given, memoizes ``_backoff_exponent`` per agent for
    the caller's duration -- :func:`effective_throttle_view` threads ONE cache
    through both its scalar and active-map computations so the same agent's
    events-file scan runs once, not twice (the exponent depends only on
    ``log_dir``'s contents and ``agent``, never on ``classification``/``reset_at_epoch``,
    so this is a pure memoization, not an approximation)."""
    if _exponent_cache is not None:
        if agent not in _exponent_cache:
            _exponent_cache[agent] = _backoff_exponent(log_dir, agent)
        exponent = _exponent_cache[agent]
    else:
        exponent = _backoff_exponent(log_dir, agent)
    return _extend_reset(classification, reset_at_epoch, exponent)


def _active_throttles(
    log_dir: Path, *, clock: Clock = SYSTEM_CLOCK, _exponent_cache: dict[str, int] | None = None
) -> dict[str, TransientErrorState]:
    """Currently-active throttles keyed by AGENT label (binary basename), one entry
    per agent whose latest transient event is an unrecovered ``detected`` with its
    reset still in the future.

    Rate limits are per-provider (per API key / agent binary), not per-phase — so the
    serve skip-policy routes around EVERY phase sharing a throttled agent while a
    healthy agent's phase keeps running. Restart-safe (events-derived). The effective
    reset applies the events-derived exp-backoff ladder (:func:`_events_derived_reset`)
    so a permanently-failing agent escalates here too, not just on the wait/back_off
    path — ONE ladder, shared by both regimes."""
    now = clock.epoch()
    active: dict[str, TransientErrorState] = {}
    for agent, detected in _latest_transient_per_agent(log_dir).items():
        if detected is None:
            continue
        classification = str(detected.get("classification", "rate_limit_account"))
        reset_at = _events_derived_reset(
            log_dir,
            agent,
            classification,
            _coerce_int(detected.get("reset_at_epoch"), 0),
            _exponent_cache=_exponent_cache,
        )
        if reset_at <= now:
            continue
        active[agent] = TransientErrorState(
            reset_at_epoch=reset_at,
            classification=classification,
            agent=agent,
            since_round=_coerce_int(detected.get("round_num"), 0),
            phase=str(detected.get("phase", "")),
        )
    return active


def round_was_mem_terminated(log_dir: Path) -> bool:
    """True iff the round that JUST ran was killed by ``_spawn_round``'s
    mid-round memory-pressure hard floor (``round_mem_terminated``) rather
    than a genuine crash — so ``serve_cmd.cmd`` can excuse it from the
    crash-loop breaker exactly like an active throttle (flat back-off,
    ``consecutive`` reset to 0), the same treatment ``ENV_BATTERY_EXIT``
    already gets. A mem-terminated round can be killed within the first ~10s
    (the mid-round check's own cadence) — far under the crash-loop breaker's
    60s "short crash" window — so, unlike a wall-clock-ceiling wedge (whose
    long duration alone dodges the breaker), this needs an explicit signal.

    Scoped to "this round" (not some earlier one) by comparing the newest
    ``round_mem_terminated`` event's timestamp against the newest
    ``round_substrate_before``'s: ``round_substrate_before(N)`` always
    precedes round N's own attempt, and no later round's
    ``round_substrate_before(N+1)`` has been emitted yet at the point
    ``cmd()`` runs this check (right after ``_spawn_round`` returns) —
    ``round_substrate_after``, also emitted before this check runs, cannot
    serve as that boundary, since it always comes AFTER any
    ``round_mem_terminated`` within the very round it is scoping. ``>=``, not
    ``>``: a fast loop (or millisecond-resolution ties) can legitimately stamp
    both events in the same millisecond; erring toward "this round's" on a tie
    only risks over-excusing, never mistaking a genuine crash for a rescue."""
    newest_before_ts: str | None = None
    newest_terminated_ts: str | None = None
    for path in sorted(log_dir.glob("events-*.jsonl"))[-2:]:
        for ev in _iter_events(path):
            ts = ev.get("ts")
            if not ts:
                continue
            kind = ev.get("event")
            if kind == ROUND_SUBSTRATE_BEFORE:
                newest_before_ts = ts
            elif kind == ROUND_MEM_TERMINATED:
                newest_terminated_ts = ts
    if newest_before_ts is None or newest_terminated_ts is None:
        return False
    return parse_iso_ms(newest_terminated_ts) >= parse_iso_ms(newest_before_ts)


def mem_loop_events_in_window(log_dir: Path, clock: Clock, window_s: int) -> int:
    """Count ``mem_loop`` events in the events tail stamped within the last
    ``window_s`` seconds of ``clock.epoch()`` — events-derived, no state file,
    same tail-reconstruction shape as :func:`round_was_mem_terminated` above.

    Feeds ``_serve_round.round_outcome_exit_code``'s cross-restart escalation
    (0.2.16 Task 5): ``MEM_LOOP_EXIT`` (71) alone resets on every serve
    process restart, so a host stuck in sustained pressure respawns into the
    identical loop forever (field-confirmed: NRestarts climbs, never
    converges). Counting PRIOR occurrences in a bounded window — rather than
    an all-time total, or a restart-local counter — gives "ages out on its
    own after a sustained-healthy stretch" for free: an old mem_loop episode
    outside the window simply stops counting, no explicit reset needed."""
    cutoff = clock.epoch() - window_s
    count = 0
    for path in sorted(log_dir.glob("events-*.jsonl"))[-2:]:
        for ev in _iter_events(path):
            if ev.get("event") != MEM_LOOP:
                continue
            ts = ev.get("ts")
            if ts and parse_iso_ms(ts).timestamp() >= cutoff:
                count += 1
    return count


def effective_throttle_view(
    log_dir: Path, *, clock: Clock = SYSTEM_CLOCK
) -> tuple[TransientErrorState | None, dict[str, TransientErrorState]]:
    """The scalar throttle view + the active-by-agent map, composed once.

    Both the scalar (:func:`_check_throttle_state`) and ``active``
    (:func:`_active_throttles`) already read the SAME ladder-extended reset
    (:func:`_events_derived_reset`), so the swap below is now a no-op in the common
    case — it stays as a backstop for the one place the two functions still scan
    differently: the scalar is the single GLOBAL-latest transient event (early-exits
    at the first monthly file holding any transient), while ``active`` merges BOTH
    monthly files PER AGENT. When the scalar's agent has an entry in ``active``, swap
    the scalar for that entry so every consumer sees the identical value the skip loop
    gates on, even in that edge case.

    A shared exponent cache is threaded through both calls below, so an agent
    the scalar and the active map both need (the common case) gets its
    events-file scan run once, not twice — see :func:`_events_derived_reset`.

    Separately, the global-latest scalar can be None while a sibling agent is
    still throttled (the newest transient event is another agent's recovered).
    When that happens fall back to the active throttle that clears LAST, so the
    scalar fields stay coherent. Both :func:`api.peek` and
    ``http_progress._rate_limit_state`` consume this — the two had drifted (peek had
    the fallback, http_progress did not)."""
    exponent_cache: dict[str, int] = {}
    throttle = _check_throttle_state(log_dir, clock=clock, _exponent_cache=exponent_cache)
    active = _active_throttles(log_dir, clock=clock, _exponent_cache=exponent_cache)
    if throttle is not None and throttle.agent in active:
        throttle = active[throttle.agent]
    elif throttle is None and active:
        throttle = max(active.values(), key=lambda s: s.reset_at_epoch)
    return throttle, active


def _check_throttle_state(
    log_dir: Path, *, clock: Clock = SYSTEM_CLOCK, _exponent_cache: dict[str, int] | None = None
) -> TransientErrorState | None:
    """Scan events.jsonl tail for latest unmatched transient error.

    Reads `transient_error_detected` / `transient_error_recovered` event names.
    Returns TransientErrorState if currently throttled (reset still in future,
    no matching recovered after). Restart-safe.

    The reset is the ladder-EXTENDED one (:func:`_events_derived_reset`), the same
    :func:`_active_throttles` computes — not the emitter's raw ``reset_at_epoch`` —
    so this scalar (serve's loop-top non-skip gate) never disagrees with the skip
    path / crash-loop excuse / peek about whether an estimated-class transient is
    still active.

    ``clock`` supplies the wall clock the reset is compared against — inject a
    ``FakeClock`` in tests to pin an exact instant. Only the reset comparison
    reads it; the event scan is pure. ``_exponent_cache`` — see
    :func:`_events_derived_reset` — lets :func:`effective_throttle_view` share
    the backoff-exponent scan with :func:`_active_throttles`.
    """
    now = clock.epoch()
    latest_detected = _latest_unrecovered_detected(log_dir)
    if latest_detected is None:
        return None
    agent = str(latest_detected.get("agent", "unknown"))
    classification = str(latest_detected.get("classification", "rate_limit_account"))
    reset_at = _events_derived_reset(
        log_dir,
        agent,
        classification,
        _coerce_int(latest_detected.get("reset_at_epoch"), 0),
        _exponent_cache=_exponent_cache,
    )
    if reset_at <= now:
        return None  # Ladder-extended reset already passed without recovery emit

    return TransientErrorState(
        reset_at_epoch=reset_at,
        classification=classification,
        agent=agent,
        since_round=_coerce_int(latest_detected.get("round_num"), 0),
        phase=str(latest_detected.get("phase", "")),
    )


def _elapsed_s(since_epoch: float, *, clock: Clock = SYSTEM_CLOCK) -> int:
    """Whole seconds from ``since_epoch`` (a clock epoch) to now, clamped at 0.
    The single elapsed-seconds computation behind every ``throttled_for_s`` — one
    clamp guards both the skip breadcrumb and the back-off recovered event against
    a backward wall-clock step yielding a negative duration."""
    return max(0, int(clock.epoch() - since_epoch))


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
    return _elapsed_s(detected.timestamp(), clock=clock)


def pending_recovered(
    log_dir: Path,
    *,
    clock: Clock = SYSTEM_CLOCK,
    active: dict[str, TransientErrorState] | None = None,
) -> list[tuple[str, str, int]]:
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
    a recovered).

    An agent is reported cleared only once its EXP-BACKOFF-EXTENDED reset has
    passed (i.e. it has dropped out of :func:`_active_throttles`), never merely
    its raw emitter reset — otherwise the skip loop would emit ``recovered`` while
    still gating the agent, flattening the ladder.

    ``active`` lets a caller that already computed :func:`_active_throttles` this
    cycle (the skip loop does) pass it in, avoiding a second events-directory
    rescan; omitted, it is computed here."""
    if active is None:
        active = _active_throttles(log_dir, clock=clock)
    cleared: list[tuple[str, str, int]] = []
    for agent, detected in _latest_transient_per_agent(log_dir).items():
        if detected is None or agent in active:
            continue  # None = latest was a recovered; in active = extended reset still future
        cleared.append(
            (
                agent,
                str(detected.get("classification", "rate_limit_account")),
                _throttled_for_s(detected.get("ts"), clock=clock),
            )
        )
    return cleared


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
    ``api_timeout``): derives the exponent from the persisted event stream
    (``_backoff_exponent``), computes duration = base × 2^min(n, _EXP_CAP), caps
    at _ABSOLUTE_CAP_S, emits ``transient_error_backoff_capped`` if multiplier >
    1 or capped.
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

    # Estimated class: apply exp backoff. Single source: the exponent is derived
    # from the persisted event stream, not a module counter — so the skip path
    # (via _active_throttles → _backoff_exponent) shares the SAME ladder and a
    # restart mid-outage resolves the same n instead of double-applying.
    base = _BACK_OFF_DEFAULTS[classification]
    n = _backoff_exponent(log_dir, agent)
    multiplier = 2 ** min(n, _EXP_CAP)
    extended_duration = base * multiplier
    capped_by_absolute_max = extended_duration > _ABSOLUTE_CAP_S
    applied_duration = min(extended_duration, _ABSOLUTE_CAP_S)
    applied_reset_at = int(clock.epoch()) + applied_duration

    new_count = n + 1

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


_BACK_OFF_CAP_S = 28800  # 8h — defensive cap; 1.6× the 5h-window
_BACK_OFF_JITTER_MIN_S = 5
_BACK_OFF_JITTER_MAX_S = 30


def _interruptible_sleep(
    total_s: float,
    stop: dict[str, bool],
    *,
    clock: Clock = SYSTEM_CLOCK,
    chunk_s: int = 30,
    should_stop: Callable[[], bool] | None = None,
    deadline_epoch: float | None = None,
) -> bool:
    """Sleep ``total_s`` in ``<= chunk_s`` slices, re-checking ``stop`` (and, if given,
    ``should_stop()``) at each boundary; return True iff ``stop["requested"]`` OR
    ``should_stop()`` cut it short. Shared by the serve restart delay and
    :func:`_apply_back_off` so a SIGTERM or a stop_file lands within one chunk instead
    of after the full sleep (e.g. the 8h back-off cap).

    ``should_stop`` matches :func:`_pause_poll`'s contract (serve_cmd.py): a zero-arg
    predicate the caller closes over its own stop_file check with, so this module never
    needs to know what "should stop" means beyond calling it.

    Counts down the *intended* nap per slice rather than measuring a wall/monotonic
    deadline: NTP-step immune (no clock read for the deadline) AND does not busy-spin
    when ``clock.sleep`` is a no-op (a test patching ``time.sleep`` runs it instantly
    instead of looping until real time advances).

    ``deadline_epoch``, when given, additionally re-checks ``clock.epoch() >=
    deadline_epoch`` at each chunk boundary and returns False (completed, NOT
    interrupted) the moment it's reached — mirroring the skip path's self-correcting
    ``clock.epoch() >= wake_epoch`` (serve_cmd.py:251). Only :func:`_apply_back_off`
    passes this: its ``total_s`` is computed against the caller's OWN clock reading at
    call time, which on an RTC-less host booting hours behind is stale, inflating
    ``total_s`` well past the real wall-clock target; re-checking the target itself
    (not just counting down the inflated duration) lets an NTP correction landing
    mid-sleep wake it early instead of riding out the full pre-computed nap. The plain
    restart-delay sleep has no such target and never passes this."""
    remaining = float(total_s)
    while remaining > 0:
        if stop["requested"] or (should_stop is not None and should_stop()):
            return True
        if deadline_epoch is not None and clock.epoch() >= deadline_epoch:
            return False
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
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Sleep until adjusted reset_at + jitter, then emit recovered. Returns True iff
    ``stop["requested"]`` OR ``should_stop()`` cut the sleep short before it completed —
    an interrupted back-off, where the caller must break WITHOUT treating the throttle
    as recovered: the reset has not passed, so a recovered breadcrumb would poison
    restart-safe state.

    The sleep is chunked and re-checks ``stop`` (and ``should_stop``) at each boundary
    (see :func:`_interruptible_sleep`), so a SIGTERM or a stop_file lands within one
    chunk instead of after a multi-hour window. It ALSO re-checks the wall-clock
    deadline itself each chunk (RTC-less back-off): an already-expired throttle on a
    host whose clock is still catching up via NTP wakes as soon as the correction
    lands, instead of sleeping out a duration computed against the stale clock.

    For estimated-class classifications (rate_limit_model / api_transient_5xx /
    api_timeout), applies exp backoff on consecutive failures via
    :func:`compute_adjusted_reset_at`. For server-authoritative rate_limit_account, the
    original reset_at_epoch is used verbatim. The 8h cap is a last-line defense against a
    malformed (far-future) reset epoch (e.g. a manual event with a far-future ts) — it
    bounds the SLEEP, not the deadline re-check, so a legitimate-but-clock-skewed
    target can still wake the capped sleep early.
    """
    from agent_runner._emit import (
        emit_transient_error_backoff_capped,
        emit_transient_error_recovered,
    )

    # throttle.reset_at_epoch is already ladder-EXTENDED (_check_throttle_state
    # applies _events_derived_reset before constructing it, by design — see its
    # docstring). compute_adjusted_reset_at's own emitted event needs the RAW
    # detector reset for original_reset_at_epoch, not this already-extended
    # value, or a 2nd+ consecutive failure would report an inflated "original"
    # (0.3's structured-event consumers would inherit the wrong field). Re-read
    # the same latest-unrecovered-detected event _check_throttle_state itself
    # scanned (no write happens between that call and this one) and fall back
    # to the extended value only if it's since disappeared or names another agent.
    raw_detected = _latest_unrecovered_detected(log_dir)
    raw_reset_at_epoch = throttle.reset_at_epoch
    if raw_detected is not None and str(raw_detected.get("agent", "unknown")) == throttle.agent:
        raw_reset_at_epoch = _coerce_int(
            raw_detected.get("reset_at_epoch"), throttle.reset_at_epoch
        )

    adjusted_reset_at, _consecutive_count, _capped = compute_adjusted_reset_at(
        classification=throttle.classification,
        original_reset_at_epoch=raw_reset_at_epoch,
        agent=throttle.agent,
        log_dir=log_dir,
        clock=clock,
    )

    # The real wall-clock target (+ jitter) — re-checked every chunk below so an NTP
    # correction landing mid-sleep wakes this early, even under the magnitude cap.
    deadline_epoch = adjusted_reset_at + random.uniform(
        _BACK_OFF_JITTER_MIN_S, _BACK_OFF_JITTER_MAX_S
    )
    requested = deadline_epoch - clock.epoch()
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
    if _interruptible_sleep(
        sleep_s,
        stop,
        clock=clock,
        chunk_s=chunk_s,
        should_stop=should_stop,
        deadline_epoch=deadline_epoch,
    ):
        return True  # SIGTERM/stop_file during back-off — leave throttle active, no breadcrumb

    emit_transient_error_recovered(
        log_dir,
        classification=throttle.classification,
        agent=throttle.agent,
        throttled_for_s=_elapsed_s(sleep_start, clock=clock),
    )
    return False
