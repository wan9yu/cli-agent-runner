"""Per-round outcome fold — the ``RoundOutcome`` struct and the three per-round
verdict readers (mem-terminated / no-progress) that the serve loop computes ONCE
per round. Carved out of ``_throttle.py`` (0.2.18) to buy that module headroom
under the 1000-line gate and to be the home for 0.3's per-agent capability.

``_tail_events`` still lives in ``_throttle`` (many throttle readers share it);
it is imported LAZILY inside :func:`round_outcome` so ``_throttle`` can re-export
these four names from its own bottom without a top-level import cycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runner.events import (
    AGENT_USAGE_RECORDED,
    ROUND_MEM_TERMINATED,
    ROUND_SUBSTRATE_BEFORE,
    TRANSIENT_ERROR_DETECTED,
    TRANSIENT_ERROR_RECOVERED,
    parse_iso_ms,
)


@dataclass(frozen=True)
class RoundOutcome:
    """Every PER-ROUND-scoped verdict :func:`round_was_mem_terminated` and
    :func:`round_had_no_progress` need, folded from ONE :func:`_tail_events`
    scan (0.2.17 Task 1) instead of the pre-0.2.17 three separate ones (those
    two functions' own scans, plus the throttle check's
    :func:`_latest_transient_per_agent` scan serve_cmd ran back-to-back with
    them every round).

    Deliberately does NOT include :func:`mem_loop_events_in_window` (give-up-only,
    needs its own clock cutoff — a different axis than "this round") or
    :func:`_backoff_exponent` (agent-scoped, keyed by a caller-supplied agent, not
    "the round that just ran") — both stay on :func:`_tail_events` directly."""

    mem_terminated: bool
    usage_capable: bool
    newest_usage_ts: str | None
    newest_substrate_before_ts: str | None
    latest_transient_per_agent: dict[str, Any]


def round_outcome(log_dir: Path) -> RoundOutcome:
    """Single-pass fold over :func:`_tail_events` computing :class:`RoundOutcome`.
    Callers: :func:`round_was_mem_terminated`, :func:`round_had_no_progress` (each
    accepts a precomputed ``outcome=`` to skip re-scanning), and
    ``serve_cmd.cmd()``'s post-round block, which computes this ONCE per round
    (INVARIANT 3) and reuses it for the mem-terminated / throttle / no-progress
    checks that used to scan separately."""
    from agent_runner._throttle import _tail_events  # lazy: breaks the re-export cycle

    newest_before_ts: str | None = None
    newest_terminated_ts: str | None = None
    newest_usage_ts: str | None = None
    usage_capable = False
    transient: dict[str, Any] = {}
    for ev in _tail_events(log_dir):
        kind = ev.get("event")
        # INVARIANT 2: latest_transient_per_agent keys by str(agent), no ts guard,
        # forward old->new merge (_tail_events yields oldest-file-first) — identical
        # to the pre-0.2.17 _latest_transient_per_agent copy this replaces.
        if kind == TRANSIENT_ERROR_DETECTED:
            transient[str(ev.get("agent", "unknown"))] = ev
        elif kind == TRANSIENT_ERROR_RECOVERED:
            transient[str(ev.get("agent", "unknown"))] = None
        # INVARIANT 1: usage_capable set UNCONDITIONALLY here, on the raw kind check,
        # BEFORE the `ts` guard below — matches the pre-refactor round_had_no_progress
        # (usage_capable must arm even off a ts-less agent_usage_recorded event).
        if kind == AGENT_USAGE_RECORDED:
            usage_capable = True
        ts = ev.get("ts")
        if not ts:
            continue
        if kind == ROUND_SUBSTRATE_BEFORE:
            newest_before_ts = ts
        elif kind == ROUND_MEM_TERMINATED:
            newest_terminated_ts = ts
        elif kind == AGENT_USAGE_RECORDED:
            newest_usage_ts = ts
    # round_was_mem_terminated's own scoping logic (see its docstring): >=, not >,
    # since a fast loop can legitimately stamp both events in the same millisecond.
    mem_terminated = (
        newest_before_ts is not None
        and newest_terminated_ts is not None
        and parse_iso_ms(newest_terminated_ts) >= parse_iso_ms(newest_before_ts)
    )
    return RoundOutcome(
        mem_terminated=mem_terminated,
        usage_capable=usage_capable,
        newest_usage_ts=newest_usage_ts,
        newest_substrate_before_ts=newest_before_ts,
        latest_transient_per_agent=transient,
    )


def round_was_mem_terminated(log_dir: Path, *, outcome: RoundOutcome | None = None) -> bool:
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
    only risks over-excusing, never mistaking a genuine crash for a rescue.

    ``outcome``, when given (the serve post-round block's one-scan path — see
    :class:`RoundOutcome`), is used verbatim instead of triggering a fresh
    :func:`round_outcome` scan; every existing caller/test omits it and gets the
    pre-0.2.17 from-scratch-scan behavior unchanged."""
    if outcome is None:
        outcome = round_outcome(log_dir)
    return outcome.mem_terminated


def round_had_no_progress(
    log_dir: Path,
    *,
    returncode: int,
    duration_s: float,
    threshold_s: float,
    throttle_active: bool = False,
    outcome: RoundOutcome | None = None,
) -> bool:
    """True iff the round that JUST ran exited 0, finished fast, but never
    reached the model -- pi (and CLIs like it: see builtin_plugins/pi.py's
    "pi exits 0 on provider failure") can exit 0 on an auth failure or an
    exhausted-retries outage that ``_round_ok = exit_code == 0`` (api_types.py)
    reads as clean, so this needs its own events-derived signal (feeding
    ``_serve_policy._no_progress_decision``'s "clean-but-no-progress" streak)
    exactly parallel to :func:`round_was_mem_terminated`'s mem-terminated one.

    TWO gates guard the verdict against over-firing on healthy deployments
    (0.2.16 fix-wave CRITICAL #1):

    1. ``throttle_active`` (the SAME value ``cmd()`` already computed for
       ``post_round_decision`` -- caller threads it through, never
       recomputed here): a provider outage that exhausts retries and exits 0
       (429/503) is ``transient_error_detected`` and excused from the
       crash-loop breaker; without this gate it would be double-counted here
       as "no progress" and stop the loop instead of riding the back-off.
    2. Usage-capability: armed ONLY when at least one ``agent_usage_recorded``
       event exists anywhere in the scanned tail. Some CLIs (kimi -- see
       builtin_plugins/kimi.py; aider; any custom ``[agent] command`` with no
       usage-emitting plugin) never emit usage BY DESIGN -- for those, "no
       usage this round" is indistinguishable from "normal for this CLI", so
       arming would stop a perfectly healthy deployment after 5 fast clean
       rounds. If the CLI's plugin stack ever emits usage (pi does on a good
       round), a round with none is genuine no-progress and still trips --
       this makes the breaker CLI-adaptive with no config descriptor.

    INVARIANT 4: short-circuits on ``returncode != 0`` or ``duration_s >=
    threshold_s`` (or ``throttle_active``) BEFORE touching ``outcome`` at all --
    still ahead of any events-tail scan / ``newest_usage_ts`` comparison, exactly
    as the pre-0.2.17 single-scan version did: a non-zero exit already has its
    own crash-loop signal, and a slow round (even with no usage) is not a TIGHT
    loop -- a wedged/hung round already has its own signal
    (``round_supervisor_wedged``), so this floor is specifically the fast spin.

    "Never reached the model" = no ``agent_usage_recorded`` event stamped at
    or after this round's ``round_substrate_before`` -- the CLI plugins only
    emit usage when the round actually consumed tokens (e.g.
    ``builtin_plugins/pi.py``'s ``_aggregate_usage`` returns ``None`` on
    all-zero usage), so an auth failure or exhausted retries leaves no such
    event. Same round-scoping shape as :func:`round_was_mem_terminated`
    (newest-event-timestamp comparison against the newest
    ``round_substrate_before``), with the comparison inverted: no progress
    means the newest usage event is EITHER absent OR older than this round's
    own start.

    ``outcome``, when given (the serve post-round block's one-scan path), is used
    verbatim instead of triggering a fresh :func:`round_outcome` scan — see
    :func:`round_was_mem_terminated`."""
    if throttle_active or returncode != 0 or duration_s >= threshold_s:
        return False
    if outcome is None:
        outcome = round_outcome(log_dir)
    if not outcome.usage_capable:
        return False  # this CLI/plugin stack has never emitted usage -- can't arm
    if outcome.newest_substrate_before_ts is None:
        return False
    if outcome.newest_usage_ts is None:
        return True
    return parse_iso_ms(outcome.newest_usage_ts) < parse_iso_ms(outcome.newest_substrate_before_ts)
