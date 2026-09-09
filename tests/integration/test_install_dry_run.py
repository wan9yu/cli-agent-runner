from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import api
from agent_runner.service_unit import monitor_unit_filename, serve_unit_filename


def test_install_should_write_both_unit_files_when_monitor_requested(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    fake_systemd = tmp_git_repo / "systemd"
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: fake_systemd)
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.api._check_user_systemd_available", lambda: None)
    monkeypatch.setattr(
        "agent_runner.api._agent_runner_script_path",
        lambda: tmp_git_repo / "fake-agent-runner",
    )

    result = api.install(tmp_git_repo, system=False, with_monitor=True)

    assert result.unit_path.exists()
    assert result.monitor_unit_path is not None
    assert result.monitor_unit_path.exists()
    project = tmp_git_repo.name
    body = result.unit_path.read_text()
    assert serve_unit_filename(project) in str(result.unit_path)
    assert "ExecStart" in body
    assert "TimeoutStopSec=" in body
    monitor_body = result.monitor_unit_path.read_text()
    assert monitor_unit_filename(project) in str(result.monitor_unit_path)
    assert "agent-runner monitor" in monitor_body
