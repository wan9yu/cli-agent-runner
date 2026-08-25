from datetime import UTC, datetime, timedelta

from agent_runner import monitor


def _ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_stale_suppressed_while_pause_is_live():
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    old = now - timedelta(hours=2)
    events = [
        {"ts": _ts(old), "event": "round_end", "round_num": 5},
        {
            "ts": _ts(old),
            "event": "schedule_paused",
            "resume_at": "2026-08-22T12:00:00+00:00",
            "active_window": "09:00-12:00",
        },
    ]
    assert monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600) is None


def test_stale_fires_after_pause_window_should_have_ended():
    # resume_at + threshold is in the past and NO resume event → died mid-pause.
    now = datetime(2026, 8, 22, 13, 0, tzinfo=UTC)
    old = now - timedelta(hours=4)
    events = [
        {
            "ts": _ts(old),
            "event": "schedule_paused",
            "resume_at": "2026-08-22T12:00:00+00:00",
            "active_window": "09:00-12:00",
        },
    ]
    alert = monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600)
    assert alert is not None and alert.detector == "supervisor_stale"


def test_stale_fires_after_resume():
    now = datetime(2026, 8, 22, 13, 0, tzinfo=UTC)
    paused_ts = now - timedelta(hours=2)
    resumed_ts = now - timedelta(hours=1)  # strictly later than paused
    events = [
        {
            "ts": _ts(paused_ts),
            "event": "schedule_paused",
            "resume_at": "2026-08-22T12:00:00+00:00",
        },
        {"ts": _ts(resumed_ts), "event": "schedule_resumed", "paused_for_s": 3600},
    ]
    alert = monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600)
    assert alert is not None and alert.detector == "supervisor_stale"


def test_stale_suppressed_empty_resume_within_horizon():
    # Always-paused config emits resume_at="" — suppress while within paused_ts+8d.
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    old = now - timedelta(hours=2)
    events = [
        {
            "ts": _ts(old),
            "event": "schedule_paused",
            "resume_at": "",
            "active_window": "00:00-24:00",
        },
    ]
    assert monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600) is None


def test_stale_fires_empty_resume_after_8day_horizon():
    # Always-paused blind-spot fix: paused_ts+8d+threshold has passed, no resume → alarm.
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    old = now - timedelta(days=9)
    events = [
        {
            "ts": _ts(old),
            "event": "schedule_paused",
            "resume_at": "",
            "active_window": "00:00-24:00",
        },
    ]
    alert = monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600)
    assert alert is not None and alert.detector == "supervisor_stale"


def test_stale_fires_with_no_schedule_events():
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    old = now - timedelta(hours=2)
    events = [{"ts": _ts(old), "event": "round_end", "round_num": 5}]
    assert monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600) is not None


def test_null_resume_at_does_not_crash_and_falls_back_to_ts_bound():
    # A foreign/corrupted event file may carry "resume_at": null. It must not raise;
    # suppression falls back to the paused ts + 8d bound (recent pause → suppressed).
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    old = now - timedelta(hours=2)
    events = [
        {"ts": _ts(old), "event": "schedule_paused", "resume_at": None, "active_window": "x"},
    ]
    assert monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600) is None
