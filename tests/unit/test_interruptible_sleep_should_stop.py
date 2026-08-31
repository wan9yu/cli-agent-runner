"""B4: _interruptible_sleep checks a should_stop predicate per chunk (stop_file
appearing during a back-off / restart delay lands within one chunk)."""

from __future__ import annotations

from pathlib import Path

from agent_runner._throttle import _apply_back_off, _interruptible_sleep
from agent_runner.api_types import TransientErrorState
from tests._clock import FakeClock
from tests._test_helpers import read_events_for_current_month


def test_should_stop_breaks_within_one_chunk():
    clock = FakeClock()
    checks = {"n": 0}

    def should_stop() -> bool:
        checks["n"] += 1
        return checks["n"] > 2  # trip on the 3rd check

    interrupted = _interruptible_sleep(
        300, {"requested": False}, clock=clock, chunk_s=30, should_stop=should_stop
    )
    assert interrupted is True
    assert sum(clock.slept) == 60  # 2 chunks then broke, not the full 300s


def test_apply_back_off_honors_should_stop_leaves_no_recovered(tmp_path: Path):
    clock = FakeClock()
    throttle = TransientErrorState(
        reset_at_epoch=int(clock.epoch()) + 3600,
        classification="rate_limit_account",
        agent="claude",
        since_round=1,
        phase="",
    )
    interrupted = _apply_back_off(
        tmp_path,
        throttle,
        stop={"requested": False},
        clock=clock,
        should_stop=lambda: True,
    )
    assert interrupted is True
    kinds = [e.get("event") for e in read_events_for_current_month(tmp_path)]
    assert "transient_error_recovered" not in kinds  # interrupted: no false breadcrumb
