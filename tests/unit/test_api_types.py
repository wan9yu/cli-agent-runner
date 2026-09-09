from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from agent_runner import api_types as api_types_module
from agent_runner.api_types import (
    Alert,
    DirtyOutcome,
    ProjectState,
    RoundResult,
    RoundView,
    ServiceMode,
    ServiceStatus,
    SystemMetrics,
    select_path,
)


def test_api_types_should_be_frozen_dataclasses() -> None:
    classes = [
        obj
        for obj in vars(api_types_module).values()
        if dataclasses.is_dataclass(obj)
        and getattr(obj, "__module__", None) == api_types_module.__name__
    ]

    assert len(classes) >= 8, "dynamic scan should find every dataclass declared in api_types"
    for cls in classes:
        assert cls.__dataclass_params__.frozen, f"{cls.__name__} not frozen"


def test_service_mode_enum_should_have_three_values() -> None:
    assert {m.value for m in ServiceMode} == {"systemd_user", "pid_file", "none"}


def test_alert_should_have_required_fields_when_constructed() -> None:
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


def test_select_path_should_return_value_when_path_is_simple_key() -> None:
    state = SystemMetrics(mem_total_mb=8000, mem_available_mb=4000, disk_used_pct=50.0)

    assert select_path(state, "mem_available_mb") == 4000


def test_select_path_should_return_item_when_path_has_list_index() -> None:
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


def test_select_path_should_raise_keyerror_when_segment_missing() -> None:
    state = SystemMetrics(mem_total_mb=8000, mem_available_mb=4000, disk_used_pct=50.0)

    with pytest.raises(KeyError, match="nonexistent"):
        select_path(state, "nonexistent")


def test_alert_auto_action_should_be_none_string_when_default() -> None:
    a = Alert(severity="info", detector="d", message="m", context={}, ts="t")

    assert a.auto_action == "none"


def test_select_path_should_return_hook_failures_list_when_present_on_state() -> None:
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


def test_project_state_recent_hook_failures_should_default_to_empty_list() -> None:
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


def test_throttle_state_import_should_raise_importerror() -> None:
    """ThrottleState alias was deprecated 0.1.23, removed 0.1.28.

    Consumers should switch to TransientErrorState.
    """
    with pytest.raises(ImportError):
        from agent_runner.api_types import ThrottleState  # noqa: F401


def test_metrics_collect_should_return_pgrep_count_when_agent_binary_given(tmp_path, monkeypatch):
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


def test_metrics_collect_should_omit_pgrep_count_when_agent_binary_not_given(tmp_path):
    from agent_runner import metrics

    out = metrics.collect(tmp_path)

    assert "agent_process_count" not in out


def test_metrics_collect_should_return_zero_pgrep_count_when_subprocess_errors(
    tmp_path, monkeypatch
):
    """pgrep timeout / FileNotFoundError → agent_process_count = 0 (defensive)."""
    import agent_runner.metrics as _metrics_mod

    def fake_run(args, **kwargs):
        raise FileNotFoundError("pgrep not installed")

    monkeypatch.setattr(_metrics_mod.subprocess, "run", fake_run)

    out = _metrics_mod.collect(tmp_path, agent_binary="claude")

    assert out["agent_process_count"] == 0


def test_dirty_outcome_should_hold_kind_and_ref():
    o = DirtyOutcome(kind="committed", ref="abc123")

    assert o.kind == "committed"
    assert o.ref == "abc123"


def test_round_result_dirty_outcome_should_default_to_none():
    base = {
        "round_num": 1,
        "phase": None,
        "started_at": "t",
        "ended_at": "t",
        "exit_code": 0,
        "duration_s": 1.0,
        "timed_out": False,
        "log_path": Path("x"),
        "dirty_files": [],
        "stashed": False,
    }

    assert RoundResult(**base).dirty_outcome is None


def test_round_result_dirty_outcome_should_be_settable_independent_of_stashed_flag():
    base = {
        "round_num": 1,
        "phase": None,
        "started_at": "t",
        "ended_at": "t",
        "exit_code": 0,
        "duration_s": 1.0,
        "timed_out": False,
        "log_path": Path("x"),
        "dirty_files": [],
        "stashed": False,
    }

    r = RoundResult(**base, dirty_outcome=DirtyOutcome(kind="stashed", ref="sha"))

    assert r.dirty_outcome.kind == "stashed" and r.stashed is False


def test_run_result_and_round_result_ok_should_share_one_predicate() -> None:
    from agent_runner.agent_runtime import RunResult
    from agent_runner.api_types import _round_ok

    for exit_code, timed_out in [(0, False), (0, True), (1, False), (137, False)]:
        expected = _round_ok(exit_code, timed_out)
        rr = RunResult(exit_code=exit_code, duration_s=1.0, timed_out=timed_out, pid=1)
        assert rr.ok is expected
        round_res = RoundResult(
            round_num=1,
            phase=None,
            started_at="",
            ended_at="",
            exit_code=exit_code,
            duration_s=1.0,
            timed_out=timed_out,
            log_path=Path("x"),
            dirty_files=[],
            stashed=False,
        )
        assert round_res.ok is expected

    assert _round_ok(0, False) is True
    assert _round_ok(0, True) is False and _round_ok(1, False) is False
