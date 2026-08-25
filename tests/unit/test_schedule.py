from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent_runner import schedule


def test_parse_window_basic():
    w = schedule.parse_window("09:00-12:00")
    assert (w.start_min, w.end_min, w.label) == (540, 720, "09:00-12:00")


def test_window_contains_is_end_exclusive():
    w = schedule.parse_window("09:00-12:00")
    assert w.contains(540) is True  # 09:00 inclusive
    assert w.contains(719) is True  # 11:59
    assert w.contains(720) is False  # 12:00 exclusive
    assert w.contains(539) is False


def test_window_wraps_past_midnight():
    w = schedule.parse_window("22:00-06:00")
    assert w.contains(23 * 60) is True  # 23:00
    assert w.contains(5 * 60) is True  # 05:00
    assert w.contains(6 * 60) is False  # 06:00 exclusive
    assert w.contains(12 * 60) is False  # noon


def test_window_end_2400_is_end_of_day():
    w = schedule.parse_window("18:00-24:00")
    assert w.contains(23 * 60 + 59) is True
    assert w.contains(18 * 60) is True


@pytest.mark.parametrize(
    "bad",
    [
        "9:00-12:00",
        "09:00_12:00",
        "25:00-26:00",
        "09:60-10:00",
        "09:00-24:30",
        "24:00-09:00",
        "09:00-09:00",
    ],
)
def test_parse_window_rejects_malformed(bad):
    with pytest.raises(ValueError):
        schedule.parse_window(bad)


def _tz_now(tz, h, m):
    return datetime(2026, 8, 22, h, m, tzinfo=ZoneInfo(tz))


def test_should_run_pause_only():
    pause = [schedule.parse_window("09:00-12:00"), schedule.parse_window("14:00-18:00")]
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 8, 0), run_windows=[], pause_windows=pause)
        is True
    )
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 10, 0), run_windows=[], pause_windows=pause)
        is False
    )
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 12, 0), run_windows=[], pause_windows=pause)
        is True
    )
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 15, 0), run_windows=[], pause_windows=pause)
        is False
    )


def test_should_run_run_only():
    run = [schedule.parse_window("00:00-06:00")]
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 3, 0), run_windows=run, pause_windows=[])
        is True
    )
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 7, 0), run_windows=run, pause_windows=[])
        is False
    )


def test_should_run_both_pause_carves_out_of_run():
    run = [schedule.parse_window("00:00-12:00")]
    pause = [schedule.parse_window("09:00-10:00")]
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 8, 0), run_windows=run, pause_windows=pause)
        is True
    )
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 9, 30), run_windows=run, pause_windows=pause)
        is False
    )


def test_should_run_neither_is_always_true():
    assert (
        schedule.should_run(_tz_now("Asia/Shanghai", 10, 0), run_windows=[], pause_windows=[])
        is True
    )


def test_evaluate_reports_active_window_and_resume():
    pause = [schedule.parse_window("09:00-12:00")]
    d = schedule.evaluate(
        run_windows=[], pause_windows=pause, now_local=_tz_now("Asia/Shanghai", 10, 0)
    )
    assert d.paused is True
    assert d.active_window == "09:00-12:00"
    assert d.resume_at.hour == 12 and d.resume_at.minute == 0


def test_next_resume_at_none_when_currently_runnable():
    d = schedule.evaluate(
        run_windows=[], pause_windows=[], now_local=_tz_now("Asia/Shanghai", 10, 0)
    )
    assert d.paused is False and d.resume_at is None
