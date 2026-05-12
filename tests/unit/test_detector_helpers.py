"""Tests for agent_runner.detector_helpers — three production-tested helpers."""

from __future__ import annotations

import time
from pathlib import Path

from agent_runner.api_types import (
    ProjectState,
    RoundView,
    ServiceMode,
    ServiceStatus,
    SystemMetrics,
)
from agent_runner.detector_helpers import (
    cumulative_window_check,
    dual_source_silence,
    phase_filter,
)


def _state(phase: str | None = None) -> ProjectState:
    return ProjectState(
        project="t",
        status={},
        defenses=[],
        current_round=RoundView(
            round_num=1,
            phase=phase,
            started_at="2026-01-01T00:00:00.000Z",
            duration_so_far_s=0.0,
            pid=None,
            exit_code=None,
            timed_out=None,
            log_path=Path("/tmp/x.log"),
        )
        if phase is not None
        else None,
        recent_rounds=[],
        orphan=None,
        system=SystemMetrics(mem_total_mb=1, mem_available_mb=1, disk_used_pct=0.0),
        service=ServiceStatus(mode=ServiceMode.NONE, active=False),
    )


# ----- cumulative_window_check -----


def test_given_no_events_when_window_checked_then_false() -> None:
    assert cumulative_window_check([], kind="x", window_s=60, min_count=1) is False


def test_given_enough_events_in_window_when_checked_then_true() -> None:
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    events = [
        {"event": "x", "ts": (now - _dt.timedelta(seconds=10)).isoformat().replace("+00:00", "Z")},
        {"event": "x", "ts": (now - _dt.timedelta(seconds=20)).isoformat().replace("+00:00", "Z")},
    ]
    assert cumulative_window_check(events, kind="x", window_s=60, min_count=2) is True


def test_given_events_outside_window_when_checked_then_false() -> None:
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    events = [
        {"event": "x", "ts": (now - _dt.timedelta(seconds=120)).isoformat().replace("+00:00", "Z")},
    ]
    assert cumulative_window_check(events, kind="x", window_s=60, min_count=1) is False


def test_given_wrong_kind_when_checked_then_false() -> None:
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    events = [
        {"event": "y", "ts": now.isoformat().replace("+00:00", "Z")},
    ]
    assert cumulative_window_check(events, kind="x", window_s=60, min_count=1) is False


# ----- dual_source_silence -----


def test_given_both_logs_recent_when_dual_silence_then_false(tmp_path: Path) -> None:
    sched = tmp_path / "scheduler.log"
    rnd = tmp_path / "round.log"
    sched.write_text("a")
    rnd.write_text("b")
    assert dual_source_silence(sched, rnd, threshold_s=60.0) is False


def test_given_only_scheduler_stale_when_dual_silence_then_false(tmp_path: Path) -> None:
    sched = tmp_path / "scheduler.log"
    rnd = tmp_path / "round.log"
    sched.write_text("a")
    rnd.write_text("b")
    old = time.time() - 7200
    import os as _os

    _os.utime(sched, (old, old))
    assert dual_source_silence(sched, rnd, threshold_s=60.0) is False


def test_given_both_logs_stale_when_dual_silence_then_true(tmp_path: Path) -> None:
    sched = tmp_path / "scheduler.log"
    rnd = tmp_path / "round.log"
    sched.write_text("a")
    rnd.write_text("b")
    old = time.time() - 7200
    import os as _os

    _os.utime(sched, (old, old))
    _os.utime(rnd, (old, old))
    assert dual_source_silence(sched, rnd, threshold_s=60.0) is True


def test_given_missing_files_when_dual_silence_then_true(tmp_path: Path) -> None:
    assert (
        dual_source_silence(tmp_path / "no.log", tmp_path / "also-no.log", threshold_s=60.0) is True
    )


# ----- phase_filter -----


def test_given_state_phase_in_exclude_when_filtered_then_false() -> None:
    state = _state(phase="retro")
    assert phase_filter(state, exclude_phases={"retro"}) is False


def test_given_state_phase_not_in_exclude_when_filtered_then_true() -> None:
    state = _state(phase="diverge")
    assert phase_filter(state, exclude_phases={"retro"}) is True


def test_given_state_phase_none_when_filtered_then_true() -> None:
    state = _state(phase=None)
    assert phase_filter(state, exclude_phases={"retro"}) is True
