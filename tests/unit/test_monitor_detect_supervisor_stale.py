from __future__ import annotations

from datetime import UTC, datetime

from agent_runner.monitor import detect_supervisor_stale


def _ev(ts: str, event: str = "round_end", **fields) -> dict:
    return {"event": event, "ts": ts, **fields}


NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)


def test_given_last_event_older_than_threshold_then_alerts() -> None:
    # Last event 4000s before NOW, threshold 2700s -> stale.
    events = [_ev("2026-05-21T10:53:20.000Z", round_num=5)]
    alert = detect_supervisor_stale(events, now=NOW, stale_threshold_s=2700)
    assert alert is not None
    assert alert.detector == "supervisor_stale"
    assert alert.severity == "warning"
    assert alert.auto_action == "none"
    assert "2700" in alert.message


def test_given_last_event_within_threshold_then_no_alert() -> None:
    # Last event 100s before NOW, threshold 2700s -> healthy.
    events = [_ev("2026-05-21T11:58:20.000Z", round_num=5)]
    assert detect_supervisor_stale(events, now=NOW, stale_threshold_s=2700) is None


def test_given_empty_events_then_no_alert() -> None:
    assert detect_supervisor_stale([], now=NOW, stale_threshold_s=2700) is None


def test_given_threshold_zero_then_disabled_no_alert() -> None:
    events = [_ev("2026-05-21T00:00:00.000Z", round_num=1)]  # very old
    assert detect_supervisor_stale(events, now=NOW, stale_threshold_s=0) is None
