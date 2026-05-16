"""Unit tests for detect_rate_limit_active monitor detector."""

from __future__ import annotations

import time


def test_given_active_throttle_in_events_when_detect_then_warning_alert():
    from agent_runner.monitor import detect_rate_limit_active

    future = int(time.time() + 3600)
    events = [
        {
            "event": "rate_limit_rejected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": future,
            "limit_type": "five_hour",
        },
    ]
    alert = detect_rate_limit_active(events)
    assert alert is not None
    assert alert.severity == "warning"
    assert alert.detector == "rate_limit_active"


def test_given_recovered_after_rejected_when_detect_then_no_alert():
    from agent_runner.monitor import detect_rate_limit_active

    future = int(time.time() + 3600)
    events = [
        {
            "event": "rate_limit_rejected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": future,
            "limit_type": "five_hour",
        },
        {
            "event": "rate_limit_recovered",
            "ts": "2026-05-16T00:01:00Z",
            "agent": "claude",
            "throttled_for_s": 60,
        },
    ]
    alert = detect_rate_limit_active(events)
    assert alert is None
