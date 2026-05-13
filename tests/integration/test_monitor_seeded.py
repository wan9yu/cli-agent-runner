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
    (log_dir / "metrics-2026-05.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-05-12T10:00:00.000Z",
                "event": "round_end",
                "mem_total_mb": 8000,
                "mem_available_mb": mem_avail_mb,
                "disk_used_pct": disk_pct,
                "disk_free_gb": 1.0,
            }
        )
        + "\n"
    )
    (log_dir / "status.json").write_text(json.dumps({"round_num": 0, "running": False}))


def test_given_seeded_disk_critical_when_poll_once_then_emits_auto_stop_alert(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed(tmp_git_repo, disk_pct=98.0, mem_avail_mb=4000)
    alerts = api._poll_once(tmp_git_repo, host=None)
    assert any(a.detector == "disk_critical" and a.auto_action == "stop_service" for a in alerts)


def test_given_seeded_mem_pressure_when_poll_once_then_emits_warning(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    _seed(tmp_git_repo, disk_pct=50.0, mem_avail_mb=100)
    alerts = api._poll_once(tmp_git_repo, host=None)
    assert any(a.detector == "mem_pressure" for a in alerts)


def test_given_per_phase_in_config_when_poll_once_then_detect_hung_uses_it(tmp_path) -> None:
    """_poll_once forwards cfg.runtime.round_timeout_per_phase to detect_hung."""
    import json
    from datetime import UTC, datetime, timedelta

    from agent_runner import api

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    log_dir = work_dir / "logs"
    log_dir.mkdir()
    rounds_dir = log_dir / "rounds"
    rounds_dir.mkdir()

    prompt_file = work_dir / "prompt.md"
    prompt_file.write_text("test\n")

    (work_dir / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{work_dir}"\n'
        f'log_dir = "{log_dir}"\n'
        "round_timeout_s = 1800\n"
        "[runtime.round_timeout_per_phase]\n"
        "warmup = 300\n"
        "[phases]\n"
        'list = ["warmup", "main"]\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )

    # Seed an events file with a long-running warmup round started 500s ago.
    now = datetime.now(UTC)
    started = (now - timedelta(seconds=500)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    month = now.strftime("%Y-%m")
    events_file = log_dir / f"events-{month}.jsonl"
    events_file.write_text(
        json.dumps({"event": "round_start", "round_num": 1, "phase": "warmup", "ts": started})
        + "\n"
    )

    alerts = api._poll_once(work_dir, host=None)
    hung = [a for a in alerts if a.detector == "hung"]
    detectors = [a.detector for a in alerts]
    assert hung, (
        f"expected detect_hung to fire under per-phase override (300s); got alerts={detectors}"
    )
    assert hung[0].context["round_num"] == 1
