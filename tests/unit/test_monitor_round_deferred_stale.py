"""detect_supervisor_stale's suppression set extended (Group 3 action half) to
round_deferred/round_resumed: a long memory-pressure defer must not be
mistaken for a dead supervisor, mirroring schedule_paused/schedule_resumed's
existing suppression in tests/unit/test_monitor_schedule_pause.py."""

from datetime import UTC, datetime, timedelta

from agent_runner import monitor


def _ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def test_stale_suppressed_while_a_round_deferral_is_live():
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    old = now - timedelta(hours=2)
    events = [
        {"ts": _ts(old), "event": "round_end", "round_num": 5},
        {
            "ts": _ts(old),
            "event": "round_deferred",
            "severity": "critical",
            "signal": "psi",
            "message": "PSI memory full avg10=2.0",
        },
    ]
    assert monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600) is None


def test_stale_fires_after_deferral_8day_horizon_with_no_resume():
    """round_deferred carries no resume_at (unlike schedule_paused) -- suppression
    falls back to the paused-ts + 8d horizon, so a supervisor that died mid-defer
    still eventually alarms."""
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    old = now - timedelta(days=9)
    events = [
        {
            "ts": _ts(old),
            "event": "round_deferred",
            "severity": "warning",
            "signal": "combined_low",
            "message": "mem_free_mb 10 < 16 and mem_available_mb 100 < 200",
        },
    ]
    alert = monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600)
    assert alert is not None and alert.detector == "supervisor_stale"


def test_stale_fires_after_round_resumed():
    now = datetime(2026, 8, 22, 13, 0, tzinfo=UTC)
    deferred_ts = now - timedelta(hours=2)
    resumed_ts = now - timedelta(hours=1)
    events = [
        {"ts": _ts(deferred_ts), "event": "round_deferred", "severity": "critical"},
        {"ts": _ts(resumed_ts), "event": "round_resumed", "deferred_for_s": 3600},
    ]
    alert = monitor.detect_supervisor_stale(events, now=now, stale_threshold_s=600)
    assert alert is not None and alert.detector == "supervisor_stale"
