"""Pin: the monitor's own emissions must not mask a dead supervisor.

``detect_supervisor_stale`` reads the same event stream the monitor writes into
(monitor_alert_emitted, detector_error, ...). If those self-authored kinds count
toward "last event timestamp", a monitor that is busy alerting on a live detector
(e.g. disk_warning) resets the freshness baseline every poll — hiding the fact
that the supervisor itself went silent two hours ago. This is the ouroboros one
layer up: the watcher's own noise blinds the watcher.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_runner.monitor import detect_supervisor_stale


def test_fresh_monitor_alert_does_not_reset_staleness_baseline() -> None:
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    events = [
        {"event": "round_end", "round_num": 4, "ts": "2026-08-30T10:00:00Z"},  # 2h old
        {
            "event": "monitor_alert_emitted",
            "detector": "disk_warning",
            "ts": "2026-08-30T11:59:59Z",
        },
        {"event": "detector_error", "detector": "hung", "ts": "2026-08-30T11:59:59Z"},
    ]
    a = detect_supervisor_stale(events, now=now, stale_threshold_s=1800)
    assert a is not None  # dead supervisor + live monitor emissions still alarms
    assert a.context["last_ts"] == "2026-08-30T10:00:00Z"
