from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runner import api
from agent_runner.api_types import Alert, ProjectState
from agent_runner.config import load_config


def _seed_logs(work_dir: Path) -> None:
    cfg = load_config(work_dir / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "events-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:00.000Z","event":"round_start","round_num":1}\n'
        '{"ts":"2026-05-12T10:00:01.000Z","event":"agent_exit","round_num":1,"exit_code":0,"duration_s":42.0,"timed_out":false}\n'
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end","round_num":1}\n'
    )
    (log_dir / "metrics-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end","mem_total_mb":8000,"mem_available_mb":4000,"disk_used_pct":50.0,"disk_free_gb":100.0}\n'
    )
    (log_dir / "status.json").write_text(
        json.dumps({"round_num": 1, "running": False, "last_exit_code": 0})
    )
    rounds = log_dir / "rounds"
    rounds.mkdir(parents=True, exist_ok=True)
    (rounds / "R1-2026-05-12.log").write_text("line-1\nline-2\nline-3-with-error\nline-4\nline-5\n")


def test_given_seeded_logs_when_api_peek_then_returns_project_state(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo)
    assert isinstance(state, ProjectState)
    assert len(state.defenses) == 11
    assert state.system.mem_total_mb == 8000


def test_given_state_when_peek_with_select_then_returns_subtree(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    val = api.peek(tmp_git_repo, select="system.disk_used_pct")
    assert val == 50.0


def test_given_invalid_select_when_peek_then_raises_keyerror(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    with pytest.raises(KeyError, match="nonexistent"):
        api.peek(tmp_git_repo, select="nonexistent")


def test_given_no_alerts_when_poll_once_then_returns_empty(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    alerts = api._poll_once(tmp_git_repo, host=None)
    assert alerts == []


def test_given_peek_with_round_latest_when_called_then_populates_current_round(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo, round="latest")
    assert state.current_round is not None
    assert state.current_round.round_num == 1
    assert state.current_round.exit_code == 0


def test_given_peek_with_log_flag_when_called_then_populates_log_tail(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo, round="latest", log=True)
    assert state.current_round is not None
    assert state.current_round.log_tail is not None
    assert "line-3-with-error" in state.current_round.log_tail


def test_given_peek_with_events_when_called_then_populates_recent_events(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo, events=2)
    assert len(state.recent_events) == 2
    assert state.recent_events[-1]["event"] == "round_end"


def test_given_peek_with_missing_round_when_called_then_raises_keyerror(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    with pytest.raises(KeyError, match="round 99"):
        api.peek(tmp_git_repo, round=99)


def test_given_seeded_disk_critical_when_poll_once_then_alert_present(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    (log_dir / "metrics-2026-05.jsonl").write_text(
        '{"ts":"2026-05-12T10:00:02.000Z","event":"round_end","mem_total_mb":8000,"mem_available_mb":4000,"disk_used_pct":98.5,"disk_free_gb":1.0}\n'
    )
    alerts = api._poll_once(tmp_git_repo, host=None)
    crit = [a for a in alerts if a.detector == "disk_critical"]
    assert len(crit) == 1
    assert isinstance(crit[0], Alert)
    assert crit[0].auto_action == "stop_service"


def test_given_peek_json_when_emit_then_plugins_block_has_hook_and_owned_path_keys(
    tmp_git_repo: Path,
    capsys,
) -> None:
    """0.1.8: plugins block in peek JSON includes pre/post hooks + owned_paths."""
    import json

    from agent_runner.api_types import (
        ProjectState,
        ServiceMode,
        ServiceStatus,
        SystemMetrics,
    )
    from agent_runner.cli.common import emit

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
    emit(state, json_mode=True)
    out = json.loads(capsys.readouterr().out)
    assert out["schema_version"] == "1.5"
    assert "pre_round_hooks" in out["plugins"]
    assert "post_round_hooks" in out["plugins"]
    assert "owned_paths" in out["plugins"]
    assert isinstance(out["plugins"]["pre_round_hooks"], list)
    assert isinstance(out["plugins"]["post_round_hooks"], list)
    assert isinstance(out["plugins"]["owned_paths"], list)


def test_given_events_with_hook_failures_when_state_assembled_then_filtered_to_field(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.1.8: api.peek populates ProjectState.recent_hook_failures from parsed events."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "events-2026-05.jsonl").write_text(
        '{"ts":"2026-05-13T00:00:00.000Z","event":"round_start","round_num":1}\n'
        '{"ts":"2026-05-13T00:01:00.000Z","event":"hook_failed","hook_name":"X","hook_kind":"pre"}\n'
        '{"ts":"2026-05-13T00:02:00.000Z","event":"agent_exit","exit_code":0,"round_num":1,"duration_s":1.0,"timed_out":false}\n'
        '{"ts":"2026-05-13T00:03:00.000Z","event":"hook_failed","hook_name":"Y","hook_kind":"post"}\n'
    )
    state = api.peek(tmp_git_repo)
    assert len(state.recent_hook_failures) == 2
    assert all(e["event"] == "hook_failed" for e in state.recent_hook_failures)
    names = sorted(e["hook_name"] for e in state.recent_hook_failures)
    assert names == ["X", "Y"]
