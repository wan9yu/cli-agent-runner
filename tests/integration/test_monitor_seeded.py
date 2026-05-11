from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runner import api
from agent_runner.config import load_config


def _seed(tmp_git_repo: Path, *, disk_pct: float, mem_avail_mb: int) -> None:
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "events-2026-05.jsonl").write_text("")
    (log_dir / "metrics-2026-05.jsonl").write_text(json.dumps({
        "ts": "2026-05-12T10:00:00.000Z", "event": "round_end",
        "mem_total_mb": 8000, "mem_available_mb": mem_avail_mb,
        "disk_used_pct": disk_pct, "disk_free_gb": 1.0,
    }) + "\n")
    (log_dir / "status.json").write_text(json.dumps({"round_num": 0, "running": False}))


def test_given_seeded_disk_critical_when_poll_once_then_emits_auto_stop_alert(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed(tmp_git_repo, disk_pct=98.0, mem_avail_mb=4000)
    alerts = api._poll_once(tmp_git_repo, host=None)
    assert any(a.detector == "disk_critical" and a.auto_action == "stop_service"
               for a in alerts)


def test_given_seeded_mem_pressure_when_poll_once_then_emits_warning(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed(tmp_git_repo, disk_pct=50.0, mem_avail_mb=100)
    alerts = api._poll_once(tmp_git_repo, host=None)
    assert any(a.detector == "mem_pressure" for a in alerts)
