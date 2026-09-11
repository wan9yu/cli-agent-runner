"""Per-phase scheduling (pure, clock-injected). NOT in schedule.py — this reads
:class:`~agent_runner.config.Config` (phases + per-phase profiles), whereas
``schedule`` stays a config-free time-window core.

The serve loop calls :func:`select_phase` once per round to decide which phase
(if any) to launch, whether to pause, and — on a ``phase_policy = "skip"`` —
which phases it stepped over. Statelessness is the contract: the result depends
only on ``(round_num, now, cfg, throttled_phases)`` — no status.json, no event
history. ``throttled_phases`` is INJECTED by the serve layer (like ``now_fn``);
the module never reads throttle/event state itself, which is what keeps it pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agent_runner import schedule


def rotation_index(round_num: int, n: int) -> int:
    """Zero-based phase index for ``round_num`` over ``n`` phases: the single
    ``(round_num - 1) % n`` rotation shared by the runner's ``_phase_for`` and this
    module's ``candidate_phases`` so serve and ``round`` rotate identically."""
    return (round_num - 1) % n


@dataclass(frozen=True)
class Selection:
    """Outcome of :func:`select_phase` for one round.

    - ``phase``: phase to launch (``None`` = no ``[phases]``, launch base config).
    - ``paused``: nothing runnable now; caller idles until a window opens.
    - ``resume_at`` / ``resume_phase`` / ``resume_timezone``: the earliest-opening
      paused candidate — the phase the supervisor is effectively waiting for. All
      three describe ONE candidate, so the schedule_paused payload is coherent
      (``None`` when no candidate opens within the horizon — all dropped).
    - ``skipped``: phases stepped over before a runnable one (``"skip"`` policy).
    - ``active_window``: on a pause, the ``resume_phase``'s closed-window label; on
      a skip, the rotation phase's (why the skip began) — for the payloads.
    """

    phase: str | None
    paused: bool
    resume_at: datetime | None
    resume_phase: str | None
    resume_timezone: str | None
    skipped: list[str]
    active_window: str | None


@dataclass(frozen=True)
class WindowOverlap:
    """Two agent-overriding phases whose own run-windows collide in the same
    timezone — a rotation footgun (serve alternates between them round-to-round).
    """

    phase_a: str
    phase_b: str
    window_a: str
    window_b: str


def _windows_collide(a: schedule.Window, b: schedule.Window) -> bool:
    """Two windows collide iff some (weekday, minute) is inside BOTH. Delegates
    to ``Window.contains`` — the scheduler's own day-shift/midnight-wrap
    semantics — instead of re-deriving interval/day math, so this can't drift
    from what actually decides whether a round runs (a wrapped window's
    past-midnight tail is attributed to its START day, not the calendar day
    the tail's clock-time falls on — re-deriving that independently is exactly
    what produced the false positive/negative this replaced). Config-time,
    once per boot, over a handful of windows; ``any`` short-circuits, so the
    O(7*1440) scan per pair is negligible.
    """
    return any(a.contains(d, m) and b.contains(d, m) for d in range(7) for m in range(1440))


def find_phase_window_overlaps(cfg) -> list[WindowOverlap]:
    """Pure config check: pairs of phases that BOTH override ``agent`` AND BOTH
    define their own non-empty ``run_windows`` AND share an effective timezone AND
    whose windows intersect. Timezone-mismatched pairs are skipped (no cross-tz
    math in a warning). ``pause_windows`` are ignored for now — a pause that carves
    out the overlap can over-warn, acceptable for a warning; 0.3.0's hard-error
    form must model pauses before rejecting.
    """
    phases = cfg.phases
    if phases is None:
        return []
    candidates = []
    for p in phases.list or []:
        ov = phases.overrides.get(p)
        if ov and ov.agent and ov.schedule is not None and ov.schedule.run_windows:
            sched = cfg.profile_for(p).schedule  # effective tz (inherits global), own windows
            candidates.append((p, sched))
    overlaps: list[WindowOverlap] = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            (pa, sa), (pb, sb) = candidates[i], candidates[j]
            if sa.timezone != sb.timezone:
                continue
            for wa in sa.run_windows:
                for wb in sb.run_windows:
                    if _windows_collide(wa, wb):
                        overlaps.append(WindowOverlap(pa, pb, wa.label, wb.label))
                        break
                else:
                    continue
                break
    return overlaps


def candidate_phases(cfg, round_num: int) -> list[str | None]:
    """Phases to consider this round, in preference order.

    ``"skip"`` returns the full rotation starting at this round's phase; ``"wait"``
    returns only that phase. No ``[phases]`` → ``[None]`` (base config). Shared with
    the serve loop's pause poller so the rotation order is single-sourced.
    """
    phases = cfg.phases.list
    if not phases:
        return [None]
    n = len(phases)
    k0 = rotation_index(round_num, n)
    if cfg.phases.phase_policy == "skip":
        return [phases[(k0 + i) % n] for i in range(n)]
    return [phases[k0]]


def select_phase(
    cfg,
    round_num: int,
    *,
    throttled_phases: frozenset[str] = frozenset(),
    now_fn=schedule.now_in_zone,
) -> Selection:
    """Pick the phase to run this round (or pause). Pure; clock via ``now_fn``.

    A candidate is runnable iff its schedule window is open AND it is not in
    ``throttled_phases`` (the serve layer injects the currently-throttled phase
    so ``skip`` steps over a rate-limited provider just as it steps over a closed
    window). A throttled phase owns no *window* resume — its resume is the
    throttle's ``reset_at``, owned by serve — so it is stepped over WITHOUT
    joining the earliest-opening ``best`` candidate.

    Each candidate's runnable check uses ``schedule.should_run`` (not ``evaluate``)
    to keep the multi-day resume scan out of the hot path; ``evaluate`` runs only
    for a window-closed (non-throttled) candidate, to compute its ``resume_at`` /
    window label.
    """
    order = candidate_phases(cfg, round_num)
    skipped: list[str] = []
    rotation_window: str | None = None
    # (resume_at, phase, window, timezone) of the earliest-opening closed candidate.
    best: tuple[datetime, str | None, str | None, str | None] | None = None
    for i, phase in enumerate(order):
        sched = cfg.profile_for(phase).schedule
        now = now_fn(sched.timezone)
        throttled = phase in throttled_phases
        if not throttled and schedule.should_run(
            now, run_windows=sched.run_windows, pause_windows=sched.pause_windows
        ):
            return Selection(
                phase=phase,
                paused=False,
                resume_at=None,
                resume_phase=None,
                resume_timezone=None,
                skipped=list(skipped),
                active_window=rotation_window,  # rotation phase's closed window (why we skipped)
            )
        if not throttled:
            decision = schedule.evaluate(
                run_windows=sched.run_windows, pause_windows=sched.pause_windows, now_local=now
            )
            if i == 0:
                rotation_window = decision.active_window
            if decision.resume_at is not None and (best is None or decision.resume_at < best[0]):
                # (B2) never-opening candidates return None and are dropped from the min.
                best = (decision.resume_at, phase, decision.active_window, sched.timezone)
        skipped.append(phase)
    if best is None:
        return Selection(None, True, None, None, None, [], rotation_window)
    return Selection(
        phase=None,
        paused=True,
        resume_at=best[0],
        resume_phase=best[1],
        resume_timezone=best[3],
        skipped=[],
        active_window=best[2],  # the resume-owner's window — coherent with resume_at/phase/tz
    )
