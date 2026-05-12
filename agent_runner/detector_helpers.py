"""Plugin detector helpers — three production-tested heuristic patterns.

Each helper encodes a lesson from running monitor detectors in real systems:

* ``cumulative_window_check`` — count events within a sliding window of
  wall-clock seconds. Codifies the lesson that "since cutoff drift" between
  the supervisor host and storage layer causes snapshot-time
  ``git log --since`` to miss commits at the window boundary. Cumulative
  counting from explicit timestamps is robust.

* ``dual_source_silence`` — alert only when BOTH the scheduler log and the
  current round log are stale beyond a threshold. Codifies the lesson that
  single-source silence on the scheduler log fires false "log silent"
  alerts during legitimately long rounds (the scheduler log only writes
  on round boundaries). The active-round log writes continuously, so a
  hang requires both to be stale.

* ``phase_filter`` — true if the current phase is NOT in the excluded set.
  Codifies the lesson that "0 commits in N rounds" detectors fire false
  positives on retrospective/reflection phases that intentionally produce
  zero commits.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from agent_runner.api_types import ProjectState
from agent_runner.events import parse_iso_ms


def cumulative_window_check(
    events: list[dict],
    *,
    kind: str,
    window_s: int,
    min_count: int,
) -> bool:
    """Return True if at least ``min_count`` events of ``kind`` occurred
    within the last ``window_s`` seconds (from now, UTC).

    Each event dict must have an ISO-8601 ``ts`` field with trailing ``Z``.

    Codifies the lesson: snapshot-time ``--since`` queries miss commits at
    the boundary due to wall-clock skew. Cumulative counting from explicit
    timestamps is robust.
    """
    if not events:
        return False
    now = datetime.now(UTC)
    cutoff = now.timestamp() - window_s
    count = 0
    for e in events:
        if e.get("event") != kind:
            continue
        ts = e.get("ts", "")
        try:
            ts_parsed = parse_iso_ms(ts)
        except (ValueError, AttributeError):
            continue
        if ts_parsed.timestamp() >= cutoff:
            count += 1
            if count >= min_count:
                return True
    return False


def dual_source_silence(
    scheduler_log: Path,
    round_log: Path,
    threshold_s: float,
) -> bool:
    """Return True only if BOTH log files' mtimes are older than ``threshold_s``
    seconds, OR if both files are missing.

    Codifies the lesson: single-source silence on ``scheduler.log`` fires
    false alerts during long rounds (scheduler.log only writes on round
    boundaries). The active-round log writes continuously, so a genuine
    hang requires both to be stale simultaneously.
    """
    now = time.time()
    return _file_stale(scheduler_log, now, threshold_s) and _file_stale(round_log, now, threshold_s)


def _file_stale(path: Path, now: float, threshold_s: float) -> bool:
    """True if ``path`` is missing OR its mtime is older than ``threshold_s`` from ``now``.

    Uses ``stat()`` + FileNotFoundError to avoid the TOCTOU race that ``exists()``
    followed by ``stat()`` would introduce.
    """
    try:
        return now - path.stat().st_mtime > threshold_s
    except FileNotFoundError:
        return True


def phase_filter(state: ProjectState, *, exclude_phases: set[str]) -> bool:
    """Return True if the detector should proceed for this state's phase.

    Returns False (skip) when ``state.current_round.phase`` is in
    ``exclude_phases``. Returns True (proceed) when phase is None or not in
    the excluded set.

    Codifies the lesson: "0 commits in N rounds" detectors mis-fire on
    retrospective/reflection rounds that intentionally produce zero
    commits. Plugin authors should pass an explicit set of phase names to
    skip.
    """
    if state.current_round is None:
        return True
    if state.current_round.phase is None:
        return True
    return state.current_round.phase not in exclude_phases
