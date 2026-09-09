from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_runner.api_types import Alert
from agent_runner.config import PhaseOverride
from agent_runner.monitor import (
    KNOWN_ALERT_KINDS,
    detect_disk_critical,
    detect_disk_warning,
    detect_hung,
    detect_mem_pressure,
    detect_mem_pressure_gate_inert,
    detect_network_fail,
    detect_oauth_fail,
    detect_orphan_chain,
    detect_timeout_rate,
)


def _ev(event: str, **fields) -> dict:
    return {"event": event, "ts": "2026-05-12T10:00:00.000Z", **fields}


def test_known_alert_kinds_should_contain_all_thirteen() -> None:
    expected = {
        "timeout_rate",
        "hung",
        "orphan_chain",
        "disk_warning",
        "disk_critical",
        "mem_pressure",
        "mem_signal_unavailable",
        "mem_pressure_gate_inert",
        "oauth_fail",
        "network_fail",
        "rate_limit_active",
        "anomaly_repetitive_active",
        "supervisor_stale",
    }

    assert expected == KNOWN_ALERT_KINDS


def test_detect_timeout_rate_should_return_warning_alert_when_three_of_ten_timed_out() -> None:
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


def test_detect_timeout_rate_should_exclude_grace_kills_from_the_rate() -> None:
    """A grace-kill sets timed_out but is not a hung round — it must not inflate
    the timeout rate (0.2.11)."""
    events = []
    for i in range(10):
        events.append(_ev("round_start", round_num=i))
        events.append(
            _ev(
                "agent_exit",
                round_num=i,
                timed_out=(i < 4),  # 4/10 have timed_out set...
                exit_code=-15 if i < 4 else 0,
                exit_cause="grace_kill" if i < 4 else "clean",  # ...but all are grace-kills
            )
        )
        events.append(_ev("round_end", round_num=i))

    assert detect_timeout_rate(events, window=10, threshold=0.2) is None  # 0 real timeouts


def test_detect_timeout_rate_should_return_none_when_below_threshold() -> None:
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


def test_detect_hung_should_return_alert_when_round_has_no_end_event() -> None:
    started = datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC)
    now = datetime(2026, 5, 12, 11, 0, 0, tzinfo=UTC)  # 1h later
    events = [_ev("round_start", round_num=42, ts=started.isoformat().replace("+00:00", "Z"))]

    a = detect_hung(events, now=now, factor=1.5, round_timeout_s=1800)  # 1.5 * 30min = 45min

    assert a is not None
    assert a.detector == "hung"
    assert a.context["round_num"] == 42


def test_detect_orphan_chain_should_return_alert_when_three_consecutive_orphans_stashed() -> None:
    events = [_ev("orphan_stashed", round_num=i) for i in (40, 41, 42)]

    a = detect_orphan_chain(events, threshold=3)

    assert a is not None
    assert a.detector == "orphan_chain"


def test_disk_detectors_should_return_none_when_used_pct_below_warning() -> None:
    metrics = [{"event": "round_end", "disk_used_pct": 80.0}]

    assert detect_disk_warning(metrics, threshold_pct=90.0) is None
    assert detect_disk_critical(metrics, threshold_pct=95.0) is None


def test_detect_disk_critical_should_return_auto_stop_alert_when_used_pct_above_critical() -> None:
    metrics = [{"event": "round_end", "disk_used_pct": 96.5}]

    a = detect_disk_critical(metrics, threshold_pct=95.0)

    assert a is not None
    assert a.severity == "critical"
    assert a.auto_action == "stop_service"


def test_disk_detectors_should_return_warning_only_when_used_pct_between_warning_and_critical() -> (
    None
):
    metrics = [{"event": "round_end", "disk_used_pct": 92.0}]

    w = detect_disk_warning(metrics, threshold_pct=90.0)

    assert w is not None and w.severity == "warning" and w.auto_action == "none"
    assert detect_disk_critical(metrics, threshold_pct=95.0) is None


def test_detect_mem_pressure_should_return_alert_when_available_and_free_are_both_low() -> None:
    # mem_free_mb low alongside mem_available_mb low is a genuine combined-low
    # signal; a bare mem_available_mb (the old dumb gate) now reports
    # mem_signal_unavailable instead -- see test_host_health.py for the ladder.
    from agent_runner.config import MonitorHostHealthConfig

    metrics = [{"event": "round_end", "mem_available_mb": 150, "mem_free_mb": 5}]

    a = detect_mem_pressure(metrics, cfg=MonitorHostHealthConfig(mem_avail_min_mb=200))

    assert a is not None
    assert a.detector == "mem_pressure"


def test_detect_mem_pressure_should_report_signal_unavailable_when_no_other_signal() -> None:
    # A 0.2.14-shaped entry (carries a new sample key -- psi_some_avg10 -- so it
    # is past the pre-0.2.14 grace) but with no usable signal at all: PSI None,
    # no swap history, MemFree unknown. That must report mem_signal_unavailable,
    # not silently trust the bare mem_available_mb.
    metrics = [{"event": "round_end", "mem_available_mb": 150, "psi_some_avg10": None}]

    a = detect_mem_pressure(metrics)

    assert a is not None
    assert a.detector == "mem_signal_unavailable"


def test_detect_mem_pressure_should_return_none_when_metrics_entry_predates_new_sampler() -> None:
    # First poll after upgrade: the last metrics-*.jsonl entry is pre-0.2.14
    # shape (only the old mem_available_mb, none of the new mem-sample keys). It
    # is "not yet sampled" by the new sampler, so detect_mem_pressure must grace
    # it (return None) rather than fire a spurious mem_signal_unavailable before
    # the first 0.2.14-shaped sample lands.
    metrics = [{"event": "round_end", "mem_available_mb": 150}]

    assert detect_mem_pressure(metrics) is None


def test_detect_mem_pressure_gate_inert_should_fire_when_swap_climbs_with_available_high() -> None:
    from agent_runner.config import MonitorHostHealthConfig

    metrics = [
        {"event": "round_end", "mem_available_mb": 150, "swap_sout": 0},
        {"event": "round_end", "mem_available_mb": 150, "swap_sout": 50 * 1024 * 1024},
    ]

    a = detect_mem_pressure_gate_inert(metrics, cfg=MonitorHostHealthConfig(mem_avail_min_mb=40))

    assert a is not None
    assert a.detector == "mem_pressure_gate_inert"


def test_detect_mem_pressure_gate_inert_should_return_none_when_host_is_healthy_warm_cache() -> (
    None
):
    """MemAvailable >> MemFree alone (every healthy warm-cache host) must not
    trip the self-check."""
    from agent_runner.config import MonitorHostHealthConfig

    metrics = [
        {
            "event": "round_end",
            "mem_available_mb": 6000,
            "mem_free_mb": 200,
            "swap_sout": 100,
            "psi_some_avg10": 0.0,
        }
    ]

    a = detect_mem_pressure_gate_inert(metrics, cfg=MonitorHostHealthConfig(mem_avail_min_mb=40))

    assert a is None


def test_detect_oauth_fail_should_return_auto_stop_alert_when_short_exits_match_oauth_pattern() -> (
    None
):
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


def _exit_zero_rounds(n: int = 10) -> list[dict]:
    """n short agent_exit records the text path can never match: exit code 0.

    The shape pi produces — it exits 0 even when the provider rejected the key.
    """
    return [
        _ev("agent_exit", round_num=i, duration_s=5.0, exit_code=0, timed_out=False)
        for i in range(n)
    ]


def test_detect_oauth_fail_should_auto_stop_when_structured_auth_events_present() -> None:
    """Structured evidence bypasses the exit-code gate: the agent's own output
    said the credential was rejected, so no heuristic shield is needed."""
    from agent_runner.events import AGENT_AUTH_ERROR_DETECTED

    events = _exit_zero_rounds()
    events += [
        _ev(AGENT_AUTH_ERROR_DETECTED, round_num=i, agent="pi", raw="401: invalid key")
        for i in (0, 1)
    ]

    a = detect_oauth_fail(events, {}, window=10, threshold=0.2)

    assert a is not None
    assert a.severity == "critical"
    assert a.auto_action == "stop_service"
    assert a.context["matches"] == 2


def test_detect_oauth_fail_should_return_none_when_auth_text_on_exit_zero_rounds() -> None:
    """The text path keeps its nonzero-exit gate: prose mentioning 401 in a
    round that exited cleanly is not evidence of an auth loop."""
    events = _exit_zero_rounds()
    log_tails = dict.fromkeys(range(10), "Error: 401 Unauthorized — invalid API key")

    assert detect_oauth_fail(events, log_tails, window=10, threshold=0.2) is None


def test_detect_oauth_fail_should_count_text_and_structured_matches_together() -> None:
    from agent_runner.events import AGENT_AUTH_ERROR_DETECTED

    events = _exit_zero_rounds()
    events[0] = _ev("agent_exit", round_num=0, duration_s=5.0, exit_code=1, timed_out=False)
    events.append(_ev(AGENT_AUTH_ERROR_DETECTED, round_num=5, agent="pi", raw="401: invalid key"))
    log_tails = {0: "Error: 401 Unauthorized"}

    a = detect_oauth_fail(events, log_tails, window=10, threshold=0.2)

    assert a is not None
    assert a.context["matches"] == 2


def test_detect_oauth_fail_should_return_none_when_single_structured_match_below_threshold() -> (
    None
):
    from agent_runner.events import AGENT_AUTH_ERROR_DETECTED

    events = _exit_zero_rounds()
    events.append(_ev(AGENT_AUTH_ERROR_DETECTED, round_num=3, agent="pi", raw="401: invalid key"))

    assert detect_oauth_fail(events, {}, window=10, threshold=0.2) is None


def test_detect_network_fail_should_return_warning_when_short_exits_match_network_pattern() -> None:
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


def test_alert_severity_should_be_one_of_three_values() -> None:
    a = Alert(severity="info", detector="d", message="m", context={}, ts="t")

    assert a.severity in {"info", "warning", "critical"}


def test_detect_oauth_fail_should_use_custom_patterns_when_provided():
    import re

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


def test_detect_oauth_fail_should_default_hint_to_empty_string_when_not_provided():
    """Per-CLI hints are supplied via preset files (0.1.7+); core code no
    longer hardcodes a Claude-specific hint."""
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
    assert alert.context["hint"] == ""


def test_detect_hung_should_use_phase_override_when_phase_matches() -> None:
    # Round started 500s ago in phase "warmup"; global timeout 1800, per-phase 300.
    # With factor=1.5, threshold for "warmup" = 300*1.5 = 450 → 500 > 450 → fire.
    # Without per-phase: threshold = 1800*1.5 = 2700 → 500 < 2700 → no fire.
    now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
    started_ts = "2026-05-13T11:51:40.000Z"  # 500s before now
    events = [
        {"event": "round_start", "round_num": 7, "phase": "warmup", "ts": started_ts},
    ]

    no_override = detect_hung(events, now=now, round_timeout_s=1800)
    with_override = detect_hung(
        events,
        now=now,
        round_timeout_s=1800,
        phases_overrides={"warmup": PhaseOverride(round_timeout_s=300)},
    )

    assert no_override is None
    assert with_override is not None
    assert with_override.detector == "hung"
    assert with_override.context["round_num"] == 7
    # threshold_s reflects the per-phase override (300 * 1.5), not the global (1800 * 1.5)
    assert with_override.context["threshold_s"] == pytest.approx(450.0)


def test_detect_hung_should_use_global_timeout_when_phase_field_missing() -> None:
    now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
    started_ts = "2026-05-13T11:51:40.000Z"  # 500s before now
    events = [
        {"event": "round_start", "round_num": 7, "ts": started_ts},  # no phase field
    ]

    # With phases_overrides but phase missing → fall back to global (1800*1.5=2700 → no fire)
    out = detect_hung(
        events,
        now=now,
        round_timeout_s=1800,
        phases_overrides={"warmup": PhaseOverride(round_timeout_s=300)},
    )

    assert out is None


def test_detect_hung_should_use_global_timeout_when_phase_not_in_overrides() -> None:
    now = datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)
    started_ts = "2026-05-13T11:51:40.000Z"  # 500s before now
    events = [
        {"event": "round_start", "round_num": 7, "phase": "cleanup", "ts": started_ts},
    ]

    # phases_overrides only configures "warmup", not "cleanup" → use global
    out = detect_hung(
        events,
        now=now,
        round_timeout_s=1800,
        phases_overrides={"warmup": PhaseOverride(round_timeout_s=300)},
    )

    assert out is None


def test_run_all_detectors_should_derive_stale_threshold_from_round_timeout() -> None:
    # round_timeout_s=1000 -> derived 1500s. Last event 1800s ago -> stale.
    from agent_runner.monitor import run_all_detectors

    events = [_ev("round_end", round_num=1)]  # ts 2026-05-12T10:00:00.000Z
    now = datetime(2026, 5, 12, 10, 30, 0, tzinfo=UTC)  # 1800s later

    alerts = run_all_detectors(
        events=events,
        metrics=[],
        log_tails={},
        round_timeout_s=1000,
        now=now,
    )

    assert any(a.detector == "supervisor_stale" for a in alerts)


def test_run_all_detectors_should_prefer_explicit_stale_threshold_over_derived() -> None:
    # Explicit 3600s threshold; last event 1800s ago -> NOT stale even though
    # derived (round_timeout 1000 * 1.5 = 1500) would have fired.
    from agent_runner.monitor import run_all_detectors

    events = [_ev("round_end", round_num=1)]
    now = datetime(2026, 5, 12, 10, 30, 0, tzinfo=UTC)  # 1800s later

    alerts = run_all_detectors(
        events=events,
        metrics=[],
        log_tails={},
        round_timeout_s=1000,
        supervisor_stale_threshold_s=3600,
        now=now,
    )

    assert not any(a.detector == "supervisor_stale" for a in alerts)
