from __future__ import annotations

from datetime import UTC, datetime

from agent_runner.api_types import Alert
from agent_runner.config import _DEFAULT_AUTH_HINT
from agent_runner.monitor import (
    KNOWN_ALERT_KINDS,
    detect_disk_critical,
    detect_disk_warning,
    detect_hung,
    detect_mem_pressure,
    detect_network_fail,
    detect_oauth_fail,
    detect_orphan_chain,
    detect_smoke_fail_rate,
    detect_timeout_rate,
)


def _ev(event: str, **fields) -> dict:
    return {"event": event, "ts": "2026-05-12T10:00:00.000Z", **fields}


def test_given_known_alert_kinds_when_inspected_then_contains_all_nine() -> None:
    expected = {
        "timeout_rate",
        "hung",
        "orphan_chain",
        "disk_warning",
        "disk_critical",
        "mem_pressure",
        "smoke_fail_rate",
        "oauth_fail",
        "network_fail",
    }
    assert expected == KNOWN_ALERT_KINDS


def test_given_three_of_ten_rounds_timed_out_when_detect_then_returns_warning_alert() -> None:
    events = []
    for i in range(10):
        events.append(_ev("round_start", round_num=i))
        events.append(
            _ev(
                "agent_exit",
                round_num=i,
                timed_out=(i < 3),
                exit_code=-1 if i < 3 else 0,
            )
        )
        events.append(_ev("round_end", round_num=i))
    a = detect_timeout_rate(events, window=10, threshold=0.2)
    assert a is not None
    assert a.detector == "timeout_rate"
    assert a.severity == "warning"
    assert a.context["rate"] >= 0.2


def test_given_one_of_ten_rounds_timed_out_when_detect_then_no_alert() -> None:
    events = []
    for i in range(10):
        events.append(_ev("round_start", round_num=i))
        events.append(
            _ev(
                "agent_exit",
                round_num=i,
                timed_out=(i == 0),
                exit_code=-1 if i == 0 else 0,
            )
        )
        events.append(_ev("round_end", round_num=i))
    assert detect_timeout_rate(events, window=10, threshold=0.2) is None


def test_given_round_started_no_end_when_hung_check_then_returns_alert() -> None:
    started = datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC)
    now = datetime(2026, 5, 12, 11, 0, 0, tzinfo=UTC)  # 1h later
    events = [_ev("round_start", round_num=42, ts=started.isoformat().replace("+00:00", "Z"))]
    a = detect_hung(events, now=now, factor=1.5, round_timeout_s=1800)  # 1.5 * 30min = 45min
    assert a is not None
    assert a.detector == "hung"
    assert a.context["round_num"] == 42


def test_given_three_consecutive_orphan_stashed_when_detect_then_returns_alert() -> None:
    events = [_ev("orphan_stashed", round_num=i) for i in (40, 41, 42)]
    a = detect_orphan_chain(events, threshold=3)
    assert a is not None
    assert a.detector == "orphan_chain"


def test_given_disk_used_pct_below_warning_then_no_alert() -> None:
    metrics = [{"event": "round_end", "disk_used_pct": 80.0}]
    assert detect_disk_warning(metrics, threshold_pct=90.0) is None
    assert detect_disk_critical(metrics, threshold_pct=95.0) is None


def test_given_disk_used_pct_above_critical_then_returns_auto_stop_alert() -> None:
    metrics = [{"event": "round_end", "disk_used_pct": 96.5}]
    a = detect_disk_critical(metrics, threshold_pct=95.0)
    assert a is not None
    assert a.severity == "critical"
    assert a.auto_action == "stop_service"


def test_given_disk_used_pct_above_warning_below_critical_then_warning_only() -> None:
    metrics = [{"event": "round_end", "disk_used_pct": 92.0}]
    w = detect_disk_warning(metrics, threshold_pct=90.0)
    assert w is not None and w.severity == "warning" and w.auto_action == "none"
    assert detect_disk_critical(metrics, threshold_pct=95.0) is None


def test_given_mem_available_below_threshold_when_detect_then_returns_alert() -> None:
    metrics = [{"event": "round_end", "mem_available_mb": 150}]
    a = detect_mem_pressure(metrics, threshold_mb=200)
    assert a is not None
    assert a.detector == "mem_pressure"


def test_given_no_smoke_fails_when_detect_then_no_alert() -> None:
    events = [_ev("round_end", round_num=i) for i in range(10)]
    assert detect_smoke_fail_rate(events, window=10, threshold=0.1) is None


def test_given_two_of_ten_smoke_failed_when_detect_then_returns_alert() -> None:
    events = []
    for i in range(10):
        if i < 2:
            events.append(_ev("smoke_check_failed", round_num=i, reason="x"))
        events.append(_ev("round_end", round_num=i))
    a = detect_smoke_fail_rate(events, window=10, threshold=0.1)
    assert a is not None


def test_given_short_exit_with_oauth_pattern_when_detect_then_returns_auto_stop_alert() -> None:
    events = []
    log_tails = {}
    for i in range(10):
        events.append(_ev("round_start", round_num=i))
        timed_out = False
        exit_code = 1 if i < 3 else 0
        duration = 5.0 if i < 3 else 200.0
        events.append(
            _ev(
                "agent_exit",
                round_num=i,
                duration_s=duration,
                exit_code=exit_code,
                timed_out=timed_out,
            )
        )
        events.append(_ev("round_end", round_num=i))
        if i < 3:
            log_tails[i] = "Error: 401 Unauthorized — invalid API key"
        else:
            log_tails[i] = "ok"
    a = detect_oauth_fail(events, log_tails, window=10, threshold=0.2)
    assert a is not None
    assert a.severity == "critical"
    assert a.auto_action == "stop_service"


def test_given_short_exit_with_network_pattern_when_detect_then_returns_warning_alert() -> None:
    events = []
    log_tails = {}
    for i in range(10):
        events.append(_ev("round_start", round_num=i))
        events.append(
            _ev(
                "agent_exit",
                round_num=i,
                duration_s=5.0 if i < 3 else 200.0,
                exit_code=1 if i < 3 else 0,
                timed_out=False,
            )
        )
        events.append(_ev("round_end", round_num=i))
        log_tails[i] = "connection refused" if i < 3 else "ok"
    a = detect_network_fail(events, log_tails, window=10, threshold=0.2)
    assert a is not None
    assert a.severity == "warning"
    assert a.auto_action == "none"


def test_given_alert_when_inspected_then_severity_is_string_one_of_three() -> None:
    a = Alert(severity="info", detector="d", message="m", context={}, ts="t")
    assert a.severity in {"info", "warning", "critical"}


def test_given_custom_auth_patterns_when_detect_oauth_fail_then_uses_custom():
    """Custom patterns override the default; detector matches against them."""
    import re

    from agent_runner.monitor import detect_oauth_fail

    # 10 events: short-exit (dur < 60), nonzero exit, not timed_out
    events = [
        {
            "event": "agent_exit",
            "round_num": i,
            "duration_s": 5.0,
            "exit_code": 1,
            "timed_out": False,
        }
        for i in range(10)
    ]
    log_tails = dict.fromkeys(range(10), "PROVIDER_AUTH_DENIED at line 42")
    custom_patterns = [re.compile(r"\bPROVIDER_AUTH_DENIED\b", re.IGNORECASE)]
    custom_hint = "Refresh PROVIDER_TOKEN env var"

    alert = detect_oauth_fail(
        events,
        log_tails,
        patterns=custom_patterns,
        hint=custom_hint,
    )
    assert alert is not None
    assert alert.detector == "oauth_fail"
    assert alert.context["hint"] == custom_hint


def test_given_default_patterns_when_detect_oauth_fail_then_existing_behavior():
    """Calling detect_oauth_fail without patterns/hint kwargs uses the defaults
    imported from config.py — preserves the prior claude OAuth detection."""
    from agent_runner.monitor import detect_oauth_fail

    events = [
        {
            "event": "agent_exit",
            "round_num": i,
            "duration_s": 5.0,
            "exit_code": 1,
            "timed_out": False,
        }
        for i in range(10)
    ]
    log_tails = dict.fromkeys(range(10), "Error: 401 Unauthorized")

    alert = detect_oauth_fail(events, log_tails)  # no patterns kwarg
    assert alert is not None
    assert alert.context["hint"] == _DEFAULT_AUTH_HINT
