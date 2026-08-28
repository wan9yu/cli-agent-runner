"""Per-phase scheduling (pure, clock-injected). NOT in schedule.py — this reads
:class:`~agent_runner.config.Config` (phases + per-phase profiles), whereas
``schedule`` stays a config-free time-window core.

The serve loop calls :func:`select_phase` once per round to decide which phase
(if any) to launch, whether to pause, and — on a ``phase_policy = "skip"`` —
which phases it stepped over. Statelessness is the contract: the result depends
only on ``(round_num, now, cfg)`` — no status.json, no event history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agent_runner import schedule


@dataclass(frozen=True)
class Selection:
    """Outcome of :func:`select_phase` for one round.

    - ``phase``: phase to launch (``None`` = no ``[phases]``, launch base config).
    - ``paused``: nothing runnable now; caller idles until a window opens.
    - ``resume_at``: earliest window-open across the paused candidates (``None``
      when no candidate opens within the schedule horizon — all dropped).
    - ``skipped``: phases stepped over before a runnable one (``"skip"`` policy).
    - ``active_window``: the matched closed-window label of the rotation / first
      skipped phase, for the schedule_paused / schedule_phase_skipped payload.
    """

    phase: str | None
    paused: bool
    resume_at: datetime | None
    skipped: list[str]
    active_window: str | None


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
    k0 = (round_num - 1) % n
    if cfg.phases.phase_policy == "skip":
        return [phases[(k0 + i) % n] for i in range(n)]
    return [phases[k0]]


def select_phase(cfg, round_num: int, *, now_fn=schedule.now_in_zone) -> Selection:
    """Pick the phase to run this round (or pause). Pure; clock via ``now_fn``.

    Each candidate's runnable check uses ``schedule.should_run`` (not ``evaluate``)
    to keep the multi-day resume scan out of the hot path; ``evaluate`` runs only
    for a closed candidate, to compute its ``resume_at`` / window label.
    """
    order = candidate_phases(cfg, round_num)
    skipped: list[str] = []
    skipped_windows: list[str | None] = []
    closed_resumes: list[datetime] = []
    rotation_window: str | None = None
    for i, phase in enumerate(order):
        sched = cfg.profile_for(phase).schedule
        now = now_fn(sched.timezone)
        if schedule.should_run(
            now, run_windows=sched.run_windows, pause_windows=sched.pause_windows
        ):
            return Selection(
                phase=phase,
                paused=False,
                resume_at=None,
                skipped=list(skipped),
                active_window=skipped_windows[0] if skipped_windows else None,
            )
        decision = schedule.evaluate(
            run_windows=sched.run_windows, pause_windows=sched.pause_windows, now_local=now
        )
        if i == 0:
            rotation_window = decision.active_window
        if decision.resume_at is not None:  # (B2) drop never-opening candidates
            closed_resumes.append(decision.resume_at)
        skipped.append(phase)
        skipped_windows.append(decision.active_window)
    return Selection(
        phase=None,
        paused=True,
        resume_at=min(closed_resumes) if closed_resumes else None,
        skipped=[],
        active_window=rotation_window,
    )
