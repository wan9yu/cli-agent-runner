"""Tests for anomaly_repetitive_active detector (0.1.32+)."""

from __future__ import annotations


def test_given_anomaly_event_in_recent_rounds_when_detect_then_returns_warning_alert():
    """1 anomaly_repetitive_tool event in last 5 rounds -> warning alert."""
    from agent_runner.monitor import detect_anomaly_repetitive_active

    events = [
        {"event": "round_start", "round_num": 8, "ts": "2026-05-18T10:00:00.000Z"},
        {
            "event": "anomaly_repetitive_tool",
            "round_num": 8,
            "tool_name": "Edit",
            "target": "/x.md",
            "count": 8,
            "window": 10,
            "ts": "2026-05-18T10:00:01.000Z",
        },
        {"event": "round_end", "round_num": 8, "ts": "2026-05-18T10:01:00.000Z"},
        {"event": "round_start", "round_num": 9, "ts": "2026-05-18T10:02:00.000Z"},
        {"event": "round_end", "round_num": 9, "ts": "2026-05-18T10:03:00.000Z"},
    ]
    alert = detect_anomaly_repetitive_active(events)
    assert alert is not None
    assert alert.detector == "anomaly_repetitive_active"
    assert alert.severity == "warning"
    assert "Edit" in alert.message


def test_given_anomaly_events_outside_window_when_detect_then_no_alert():
    """anomaly events older than window_rounds -> no alert."""
    from agent_runner.monitor import detect_anomaly_repetitive_active

    events = [
        {
            "event": "anomaly_repetitive_tool",
            "round_num": 1,
            "tool_name": "Edit",
            "target": "/x.md",
            "count": 8,
            "window": 10,
            "ts": "2026-05-18T10:00:00.000Z",
        },
    ] + [
        {"event": "round_start", "round_num": rn, "ts": "2026-05-18T10:00:00.000Z"}
        for rn in range(2, 20)
    ] + [
        {"event": "round_end", "round_num": rn, "ts": "2026-05-18T10:01:00.000Z"}
        for rn in range(2, 20)
    ]
    # Latest round is 19; window=5 covers rounds 15..19; anomaly at round 1 -> outside
    alert = detect_anomaly_repetitive_active(events, window_rounds=5)
    assert alert is None


def test_given_no_anomaly_events_when_detect_then_no_alert():
    """No anomaly_repetitive_tool events -> no alert."""
    from agent_runner.monitor import detect_anomaly_repetitive_active

    events = [
        {"event": "round_start", "round_num": 1, "ts": "2026-05-18T10:00:00.000Z"},
        {"event": "round_end", "round_num": 1, "ts": "2026-05-18T10:01:00.000Z"},
    ]
    alert = detect_anomaly_repetitive_active(events)
    assert alert is None
