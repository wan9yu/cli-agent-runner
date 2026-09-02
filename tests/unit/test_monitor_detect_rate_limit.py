"""Unit tests for detect_rate_limit_active monitor detector."""

from __future__ import annotations

import time


def test_given_active_throttle_in_events_when_detect_then_warning_alert():
    from agent_runner.monitor import detect_rate_limit_active

    future = int(time.time() + 3600)
    events = [
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": future,
            "classification": "rate_limit_account",
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
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": future,
            "classification": "rate_limit_account",
        },
        {
            "event": "transient_error_recovered",
            "ts": "2026-05-16T00:01:00Z",
            "agent": "claude",
            "throttled_for_s": 60,
        },
    ]
    alert = detect_rate_limit_active(events)
    assert alert is None


def test_given_log_dir_when_detect_then_uses_ladder_extended_reset(tmp_path):
    """With ``log_dir`` given, the alert reads the SAME ladder-extended reset the
    serve gate / skip path / peek converge on (agent_runner._throttle), not the
    emitter's raw reset_at_epoch -- two consecutive api_timeout detections push the
    exponent to 1, extending the reset 30s (base) past the raw one. Without
    log_dir (the default), it degrades to the raw reset for back-compat."""
    import json
    from datetime import UTC, datetime

    from agent_runner.monitor import detect_rate_limit_active

    raw_reset = 10_000
    events = [
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": raw_reset,
            "classification": "api_timeout",
        },
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:00:01Z",
            "agent": "claude",
            "reset_at_epoch": raw_reset,
            "classification": "api_timeout",
        },
    ]
    events_path = tmp_path / "events-2026-05.jsonl"
    events_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    now = raw_reset + 5  # past the raw reset, before the extended one (+30 more)
    assert detect_rate_limit_active(events, now=now) is None  # no log_dir -> raw reset

    alert = detect_rate_limit_active(events, now=now, log_dir=tmp_path)
    assert alert is not None
    expected_iso = datetime.fromtimestamp(raw_reset + 30, UTC).isoformat()
    assert alert.context["throttled_until_iso"] == expected_iso
