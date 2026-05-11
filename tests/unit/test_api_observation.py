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


def test_given_seeded_logs_when_api_peek_then_returns_project_state(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    state = api.peek(tmp_git_repo)
    assert isinstance(state, ProjectState)
    assert len(state.defenses) == 11
    assert state.system.mem_total_mb == 8000


def test_given_state_when_peek_with_select_then_returns_subtree(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    val = api.peek(tmp_git_repo, select="system.disk_used_pct")
    assert val == 50.0


def test_given_invalid_select_when_peek_then_raises_keyerror(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    with pytest.raises(KeyError, match="nonexistent"):
        api.peek(tmp_git_repo, select="nonexistent")


def test_given_no_alerts_when_poll_once_then_returns_empty(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed_logs(tmp_git_repo)
    alerts = api._poll_once(tmp_git_repo, host=None)
    assert alerts == []


def test_given_seeded_disk_critical_when_poll_once_then_alert_present(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
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
