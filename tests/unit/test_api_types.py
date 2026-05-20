from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from agent_runner.api_types import (
    Alert,
    InitResult,
    InstallResult,
    ProjectState,
    RoundView,
    ServiceMode,
    ServiceStatus,
    SystemMetrics,
    select_path,
)


def test_given_all_api_types_when_inspected_then_are_frozen_dataclasses() -> None:
    classes = (
        Alert,
        InitResult,
        InstallResult,
        ProjectState,
        RoundView,
        ServiceStatus,
        SystemMetrics,
    )
    for cls in classes:
        assert dataclasses.is_dataclass(cls), f"{cls.__name__} not a dataclass"
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} not frozen"


def test_given_service_mode_enum_when_inspected_then_has_three_values() -> None:
    assert {m.value for m in ServiceMode} == {"systemd_user", "pid_file", "none"}


def test_given_alert_when_constructed_then_has_required_fields() -> None:
    a = Alert(
        severity="warning",
        detector="timeout_rate",
        message="3/10 rounds timed out",
        context={"rate": 0.3, "threshold": 0.2},
        ts="2026-05-12T10:00:00.000Z",
        auto_action="none",
    )
    assert a.severity == "warning"
    assert a.auto_action == "none"


def test_given_select_path_dot_notation_when_resolved_then_returns_subtree() -> None:
    state = SystemMetrics(mem_total_mb=8000, mem_available_mb=4000, disk_used_pct=50.0)
    assert select_path(state, "mem_available_mb") == 4000


def test_given_select_path_with_list_index_when_resolved_then_returns_item() -> None:
    rv = RoundView(
        round_num=1,
        phase=None,
        started_at="t",
        duration_so_far_s=None,
        pid=None,
        exit_code=0,
        timed_out=False,
        log_path=Path("/x.log"),
        log_tail=None,
        recent_events=[{"event": "round_start"}, {"event": "round_end"}],
    )
    assert select_path(rv, "recent_events.0.event") == "round_start"
    assert select_path(rv, "recent_events.1.event") == "round_end"


def test_given_select_path_with_missing_segment_when_resolved_then_raises_keyerror() -> None:
    state = SystemMetrics(mem_total_mb=8000, mem_available_mb=4000, disk_used_pct=50.0)
    with pytest.raises(KeyError, match="nonexistent"):
        select_path(state, "nonexistent")


def test_given_alert_auto_action_when_default_then_is_none_string() -> None:
    a = Alert(severity="info", detector="d", message="m", context={}, ts="t")
    assert a.auto_action == "none"


def test_given_state_with_recent_hook_failures_when_select_path_then_returns_list() -> None:
    """0.1.8: peek --select recent_hook_failures resolves through select_path."""
    failures = [{"event": "hook_failed", "hook_name": "X"}]
    state = ProjectState(
        project="t",
        status={},
        defenses=[],
        current_round=None,
        recent_rounds=[],
        orphan=None,
        system=SystemMetrics(mem_total_mb=1, mem_available_mb=1, disk_used_pct=0.0),
        service=ServiceStatus(mode=ServiceMode.NONE, active=False),
        recent_events=[],
        recent_hook_failures=failures,
    )
    assert select_path(state, "recent_hook_failures") == failures


def test_given_state_default_when_constructed_then_recent_hook_failures_empty() -> None:
    """0.1.8: recent_hook_failures has a default_factory so existing callers don't break."""
    state = ProjectState(
        project="t",
        status={},
        defenses=[],
        current_round=None,
        recent_rounds=[],
        orphan=None,
        system=SystemMetrics(mem_total_mb=1, mem_available_mb=1, disk_used_pct=0.0),
        service=ServiceStatus(mode=ServiceMode.NONE, active=False),
    )
    assert state.recent_hook_failures == []


def test_throttle_state_removed() -> None:
    """ThrottleState alias was deprecated 0.1.23, removed 0.1.28.

    Consumers should switch to TransientErrorState.
    """
    with pytest.raises(ImportError):
        from agent_runner.api_types import ThrottleState  # noqa: F401


def test_metrics_collect_with_agent_binary_returns_pgrep_count(tmp_path, monkeypatch):
    """When agent_binary is supplied, collect() runs pgrep -xc and includes count."""
    import agent_runner.metrics as _metrics_mod

    class _FakeCompleted:
        def __init__(self, returncode: int, stdout: str):
            self.returncode = returncode
            self.stdout = stdout

    def fake_run(args, **kwargs):
        assert args == ["pgrep", "-xc", "claude"]
        return _FakeCompleted(returncode=0, stdout="3\n")

    monkeypatch.setattr(_metrics_mod.subprocess, "run", fake_run)
    out = _metrics_mod.collect(tmp_path, agent_binary="claude")
    assert out["agent_process_count"] == 3


def test_metrics_collect_without_agent_binary_omits_count(tmp_path):
    """Backward compat: callers that don't pass agent_binary get no field."""
    from agent_runner import metrics

    out = metrics.collect(tmp_path)
    assert "agent_process_count" not in out


def test_metrics_collect_handles_pgrep_subprocess_error_returns_zero(tmp_path, monkeypatch):
    """pgrep timeout / FileNotFoundError → agent_process_count = 0 (defensive)."""
    import agent_runner.metrics as _metrics_mod

    def fake_run(args, **kwargs):
        raise FileNotFoundError("pgrep not installed")

    monkeypatch.setattr(_metrics_mod.subprocess, "run", fake_run)
    out = _metrics_mod.collect(tmp_path, agent_binary="claude")
    assert out["agent_process_count"] == 0
