"""Unit tests for api.install helpers: script-path detection, pre-flight, --system mode."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agent_runner import api
from agent_runner.api import _agent_runner_script_path, _check_user_systemd_available

# ---------------------------------------------------------------------------
# Task 1: _agent_runner_script_path
# ---------------------------------------------------------------------------


def test_given_shutil_which_returns_path_when_called_then_returns_that_path(tmp_path):
    fake_script = tmp_path / "agent-runner"
    fake_script.write_text("#!/bin/sh\n")
    with patch("agent_runner.api.shutil.which", return_value=str(fake_script)):
        assert _agent_runner_script_path() == fake_script


def test_given_shutil_which_none_when_sysconfig_has_script_then_returns_fallback(tmp_path):
    fake_scripts = tmp_path / "scripts"
    fake_scripts.mkdir()
    fake_script = fake_scripts / "agent-runner"
    fake_script.write_text("#!/bin/sh\n")
    with patch("agent_runner.api.shutil.which", return_value=None), \
         patch("agent_runner.api.sysconfig.get_path", return_value=str(fake_scripts)):
        assert _agent_runner_script_path() == fake_script


def test_given_neither_shutil_nor_sysconfig_when_called_then_raises_filenotfounderror(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with patch("agent_runner.api.shutil.which", return_value=None), \
         patch("agent_runner.api.sysconfig.get_path", return_value=str(empty)):
        with pytest.raises(FileNotFoundError, match=r"agent-runner script not found"):
            _agent_runner_script_path()


# ---------------------------------------------------------------------------
# Task 2: _check_user_systemd_available
# ---------------------------------------------------------------------------


def test_given_no_xdg_runtime_dir_when_check_then_raises_with_hint():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match=r"XDG_RUNTIME_DIR"):
            _check_user_systemd_available()


def test_given_bus_failure_when_check_then_raises_with_hint(tmp_path):
    fake_runtime = tmp_path / "runtime"
    fake_runtime.mkdir()
    with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(fake_runtime)}):
        with patch("agent_runner.api.subprocess.run") as mock_run:
            mock_run.return_value.stderr = "Failed to connect to bus: No medium found\n"
            with pytest.raises(RuntimeError, match=r"D-Bus session"):
                _check_user_systemd_available()


def test_given_user_systemd_ok_when_check_then_returns_none(tmp_path):
    fake_runtime = tmp_path / "runtime"
    fake_runtime.mkdir()
    with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(fake_runtime)}):
        with patch("agent_runner.api.subprocess.run") as mock_run:
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            # Should not raise
            _check_user_systemd_available()


# ---------------------------------------------------------------------------
# Task 3: --system mode
# ---------------------------------------------------------------------------


def test_given_system_mode_without_root_when_install_then_raises(tmp_path):
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        "[runtime]\nwork_dir = '.'\nlog_dir = 'logs'\n[prompt]\nfile = 'p.md'\n"
    )
    with patch("agent_runner.api.os.geteuid", return_value=1000):
        with pytest.raises(RuntimeError, match=r"--system requires sudo"):
            api.install(tmp_path, system=True)


def test_given_system_mode_without_sudo_user_when_install_then_raises(tmp_path):
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        "[runtime]\nwork_dir = '.'\nlog_dir = 'logs'\n[prompt]\nfile = 'p.md'\n"
    )
    with patch("agent_runner.api.os.geteuid", return_value=0), \
         patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match=r"SUDO_USER"):
            api.install(tmp_path, system=True)


def test_given_system_mode_happy_path_when_install_then_writes_to_etc_and_does_not_start(
    tmp_path, monkeypatch
):
    # Set up a valid project directory
    work_dir = tmp_path / "myproject"
    work_dir.mkdir()
    (work_dir / "agent-runner.toml").write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        f"[runtime]\nwork_dir = '{work_dir}'\nlog_dir = 'logs'\n[prompt]\nfile = 'p.md'\n"
    )
    (work_dir / "p.md").write_text("hi")

    fake_etc = tmp_path / "etc" / "systemd" / "system"
    fake_etc.mkdir(parents=True)

    calls = []

    def fake_subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        m = type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        return m

    with patch("agent_runner.api.os.geteuid", return_value=0), \
         patch.dict(os.environ, {"SUDO_USER": "dietpi"}), \
         patch("agent_runner.api.shutil.which", return_value="/fake/agent-runner"), \
         patch("agent_runner.api.subprocess.run", side_effect=fake_subprocess_run), \
         patch("agent_runner.api._SYSTEM_UNITS_DIR", fake_etc):
        result = api.install(work_dir, system=True)

    assert result.enabled is True
    assert result.started is False

    # Verify systemctl start was NOT called
    for cmd in calls:
        assert "start" not in cmd, f"unexpected 'start' in {cmd}"
    # Verify daemon-reload and enable were called
    assert any("daemon-reload" in cmd for cmd in calls)
    assert any("enable" in cmd for cmd in calls)
