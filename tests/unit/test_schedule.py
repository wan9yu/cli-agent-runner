from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from agent_runner import schedule


def test_parse_window_basic():
    w = schedule.parse_window("09:00-12:00")
    assert (w.start_min, w.end_min, w.label) == (540, 720, "09:00-12:00")


def test_window_contains_is_end_exclusive():
    w = schedule.parse_window("09:00-12:00")
    assert w.contains(0, 540) is True  # 09:00 inclusive
    assert w.contains(0, 719) is True  # 11:59
    assert w.contains(0, 720) is False  # 12:00 exclusive
    assert w.contains(0, 539) is False


def test_window_wraps_past_midnight():
    w = schedule.parse_window("22:00-06:00")
    assert w.contains(0, 23 * 60) is True  # 23:00
    assert w.contains(0, 5 * 60) is True  # 05:00
    assert w.contains(0, 6 * 60) is False  # 06:00 exclusive
    assert w.contains(0, 12 * 60) is False  # noon


def test_window_end_2400_is_end_of_day():
    w = schedule.parse_window("18:00-24:00")
    assert w.contains(0, 23 * 60 + 59) is True
    assert w.contains(0, 18 * 60) is True


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


def test_parse_weekday_prefix_range():
    w = schedule.parse_window("Mon-Fri 09:00-12:00")
    assert w.days == frozenset({0, 1, 2, 3, 4})
    assert w.label == "Mon-Fri 09:00-12:00"


def test_parse_weekday_list_and_combo():
    assert schedule.parse_window("Sat,Sun 00:00-24:00").days == frozenset({5, 6})
    assert schedule.parse_window("Mon-Fri,Sun 09:00-10:00").days == frozenset({0, 1, 2, 3, 4, 6})


def test_parse_weekday_is_case_insensitive():
    assert schedule.parse_window("mon-fri 09:00-12:00").days == frozenset({0, 1, 2, 3, 4})


def test_bare_window_applies_every_day():
    w = schedule.parse_window("09:00-12:00")
    assert w.days == frozenset()
    assert w.contains(5, 600) is True  # Saturday still matches


@pytest.mark.parametrize("bad", ["Fri-Mon 09:00-12:00", "Xyz 09:00-12:00", "Mon- 09:00-12:00"])
def test_parse_weekday_rejects_bad(bad):
    with pytest.raises(ValueError):
        schedule.parse_window(bad)


def test_contains_respects_weekday():
    w = schedule.parse_window("Mon-Fri 09:00-12:00")
    assert w.contains(0, 600) is True  # Monday 10:00
    assert w.contains(5, 600) is False  # Saturday 10:00 — not in Mon-Fri


def test_contains_wrap_tail_belongs_to_start_day():
    w = schedule.parse_window("Fri 22:00-02:00")
    assert w.contains(4, 23 * 60) is True  # Fri 23:00
    assert w.contains(5, 1 * 60) is True  # Sat 01:00 — wrapped tail of Friday
    assert w.contains(5, 23 * 60) is False  # Sat 23:00 — Saturday not scoped


def test_should_run_deepseek_weekday_vs_weekend():
    pause = [
        schedule.parse_window("Mon-Fri 09:00-12:00"),
        schedule.parse_window("Mon-Fri 14:00-18:00"),
    ]
    mon_10 = datetime(2026, 8, 24, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # Monday
    sat_10 = datetime(2026, 8, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # Saturday
    assert schedule.should_run(mon_10, run_windows=[], pause_windows=pause) is False
    assert schedule.should_run(sat_10, run_windows=[], pause_windows=pause) is True


def test_next_resume_crosses_weekend_for_weekday_run_window():
    # run only Mon-Fri 09:00-17:00 → paused all weekend; from Sat next resume is Mon 09:00
    run = [schedule.parse_window("Mon-Fri 09:00-17:00")]
    sat_12 = datetime(2026, 8, 22, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # Saturday
    d = schedule.evaluate(run_windows=run, pause_windows=[], now_local=sat_12)
    assert d.paused is True
    assert d.resume_at.weekday() == 0 and d.resume_at.hour == 9  # Monday 09:00
