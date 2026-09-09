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


def test_agent_runner_script_path_should_return_which_result_when_shutil_which_finds_it(tmp_path):
    fake_script = tmp_path / "agent-runner"
    fake_script.write_text("#!/bin/sh\n")

    with patch("agent_runner.api.shutil.which", return_value=str(fake_script)):
        result = _agent_runner_script_path()

    assert result == fake_script


def test_agent_runner_script_path_should_return_sysconfig_fallback_when_shutil_which_returns_none(
    tmp_path,
):
    fake_scripts = tmp_path / "scripts"
    fake_scripts.mkdir()
    fake_script = fake_scripts / "agent-runner"
    fake_script.write_text("#!/bin/sh\n")

    with (
        patch("agent_runner.api.shutil.which", return_value=None),
        patch("agent_runner.api.sysconfig.get_path", return_value=str(fake_scripts)),
    ):
        result = _agent_runner_script_path()

    assert result == fake_script


def test_agent_runner_script_path_should_raise_filenotfounderror_when_neither_source_has_script(
    tmp_path,
):
    empty = tmp_path / "empty"
    empty.mkdir()

    with (
        patch("agent_runner.api.shutil.which", return_value=None),
        patch("agent_runner.api.sysconfig.get_path", return_value=str(empty)),
    ):
        with pytest.raises(FileNotFoundError, match=r"agent-runner script not found"):
            _agent_runner_script_path()


# ---------------------------------------------------------------------------
# Task 2: _check_user_systemd_available
# ---------------------------------------------------------------------------


def test_check_user_systemd_available_should_raise_when_xdg_runtime_dir_missing():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match=r"XDG_RUNTIME_DIR"):
            _check_user_systemd_available()


def test_check_user_systemd_available_should_raise_when_dbus_session_unreachable(tmp_path):
    fake_runtime = tmp_path / "runtime"
    fake_runtime.mkdir()

    with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(fake_runtime)}):
        with patch("agent_runner.api.subprocess.run") as mock_run:
            mock_run.return_value.stderr = "Failed to connect to bus: No medium found\n"
            with pytest.raises(RuntimeError, match=r"D-Bus session"):
                _check_user_systemd_available()


def test_check_user_systemd_available_should_return_none_when_systemd_is_ok(tmp_path):
    fake_runtime = tmp_path / "runtime"
    fake_runtime.mkdir()

    with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(fake_runtime)}):
        with patch("agent_runner.api.subprocess.run") as mock_run:
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0

            assert _check_user_systemd_available() is None


# ---------------------------------------------------------------------------
# Task 3: --system mode
# ---------------------------------------------------------------------------


def test_install_should_raise_when_system_mode_used_without_root(tmp_path):
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        "[runtime]\nwork_dir = '.'\nlog_dir = 'logs'\n[prompt]\nfile = 'p.md'\n"
    )

    with patch("agent_runner.api.os.geteuid", return_value=1000):
        with pytest.raises(RuntimeError, match=r"--system requires sudo"):
            api.install(tmp_path, system=True)


def test_install_should_raise_when_system_mode_used_without_sudo_user_env(tmp_path):
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        "[runtime]\nwork_dir = '.'\nlog_dir = 'logs'\n[prompt]\nfile = 'p.md'\n"
    )

    with (
        patch("agent_runner.api.os.geteuid", return_value=0),
        patch.dict(os.environ, {}, clear=True),
    ):
        with pytest.raises(RuntimeError, match=r"SUDO_USER"):
            api.install(tmp_path, system=True)


def test_install_should_write_unit_to_etc_and_not_start_when_system_mode_succeeds(
    tmp_path, monkeypatch
):
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

    with (
        patch("agent_runner.api.os.geteuid", return_value=0),
        patch.dict(os.environ, {"SUDO_USER": "dietpi"}),
        patch("agent_runner.api.shutil.which", return_value="/fake/agent-runner"),
        patch("agent_runner.api.subprocess.run", side_effect=fake_subprocess_run),
        patch("agent_runner.api._SYSTEM_UNITS_DIR", fake_etc),
    ):
        result = api.install(work_dir, system=True)

    assert result.enabled is True
    assert result.started is False

    # Verify systemctl start was NOT called
    for cmd in calls:
        assert "start" not in cmd, f"unexpected 'start' in {cmd}"
    # Verify daemon-reload and enable were called
    assert any("daemon-reload" in cmd for cmd in calls)
    assert any("enable" in cmd for cmd in calls)


# ---------------------------------------------------------------------------
# Task 4: install-clobber guard (Group C, seam 3) — same-basename sibling
# ---------------------------------------------------------------------------


def _write_project(work_dir, *, log_dir="logs"):
    work_dir.mkdir(parents=True)
    (work_dir / "p.md").write_text("hi")
    (work_dir / "agent-runner.toml").write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        f"[runtime]\nwork_dir = '{work_dir}'\nlog_dir = '{log_dir}'\n[prompt]\nfile = 'p.md'\n"
    )


def _patch_user_install(monkeypatch, units_dir):
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: units_dir)
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.api._check_user_systemd_available", lambda: None)
    monkeypatch.setattr(
        "agent_runner.api._agent_runner_script_path", lambda: units_dir / "fake-agent-runner"
    )


def test_install_should_raise_fileexistserror_when_sibling_unit_shares_basename_without_force(
    tmp_path, monkeypatch
):
    units_dir = tmp_path / "systemd"
    _patch_user_install(monkeypatch, units_dir)
    site_a = tmp_path / "site-a" / "myproj"
    site_b = tmp_path / "site-b" / "myproj"  # same basename, different location
    _write_project(site_a)
    _write_project(site_b)
    api.install(site_a, system=False)  # installs agent-runner@myproj.service for site_a

    with pytest.raises(FileExistsError, match="myproj"):
        api.install(site_b, system=False)


def test_install_should_overwrite_sibling_unit_when_force_is_set(tmp_path, monkeypatch):
    units_dir = tmp_path / "systemd"
    _patch_user_install(monkeypatch, units_dir)
    site_a = tmp_path / "site-a" / "myproj"
    site_b = tmp_path / "site-b" / "myproj"
    _write_project(site_a)
    _write_project(site_b)
    api.install(site_a, system=False)

    result = api.install(site_b, system=False, force=True)

    assert result.unit_path.exists()
    assert f"WorkingDirectory={site_b.resolve()}" in result.unit_path.read_text()


def test_install_should_not_require_force_when_reinstalling_same_project(tmp_path, monkeypatch):
    """A same-basename unit that already targets THIS work_dir (a plain
    reinstall/refresh) must not be treated as a clobber."""
    units_dir = tmp_path / "systemd"
    _patch_user_install(monkeypatch, units_dir)
    site_a = tmp_path / "site-a" / "myproj"
    _write_project(site_a)
    api.install(site_a, system=False)

    result = api.install(site_a, system=False)  # no force needed

    assert result.unit_path.exists()
