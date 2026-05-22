from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import api
from agent_runner.api_types import InitResult, ServiceMode, ServiceStatus
from agent_runner.config import PhaseOverride, PhasesConfig, load_config


def test_given_git_repo_when_api_init_then_returns_init_result(tmp_git_repo: Path) -> None:
    result = api.init(tmp_git_repo, force=False, commit=False)
    assert isinstance(result, InitResult)
    assert result.work_dir == tmp_git_repo
    assert any(f.name == "agent-runner.toml" for f in result.files_created)


def test_given_no_systemd_no_pid_when_api_status_then_returns_mode_none(tmp_git_repo: Path) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    s = api.status(tmp_git_repo)
    assert isinstance(s, ServiceStatus)
    assert s.mode == ServiceMode.NONE
    assert s.active is False


def test_given_pid_file_with_self_pid_when_status_then_active_true(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text(str(os.getpid()))
    s = api.status(tmp_git_repo)
    assert s.mode == ServiceMode.PID_FILE
    assert s.active is True
    assert s.pid == os.getpid()


def test_given_pid_file_with_dead_pid_when_status_then_active_false(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("999999999")
    s = api.status(tmp_git_repo)
    assert s.mode == ServiceMode.PID_FILE
    assert s.active is False


def test_given_pid_file_when_api_stop_then_sends_sigterm(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    with patch("agent_runner.api.send_signal_to_pid", return_value=True) as send:
        api.stop(tmp_git_repo)
        send.assert_called_with(12345, signal.SIGTERM)


def test_given_pid_file_when_api_kill_then_sends_sigterm_then_sigkill(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    with (
        patch("agent_runner.api.send_signal_to_pid", return_value=True) as send,
        patch("agent_runner.api.pid_alive", side_effect=[True, False]),
    ):
        api.kill(tmp_git_repo)
        sent = [c.args[1] for c in send.call_args_list]
        assert signal.SIGTERM in sent


def test_given_pid_file_when_api_cancel_then_sends_sigusr1(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    cfg = load_config(tmp_git_repo / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")
    with patch("agent_runner.api.send_signal_to_pid", return_value=True) as send:
        api.cancel(tmp_git_repo)
        send.assert_called_with(12345, signal.SIGUSR1)


def test_given_install_with_no_systemctl_when_called_then_returns_install_result(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    monkeypatch.setattr(
        "agent_runner.lifecycle._user_systemd_dir", lambda: tmp_git_repo / "fake-systemd"
    )
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.api._check_user_systemd_available", lambda: None)
    monkeypatch.setattr(
        "agent_runner.api._agent_runner_script_path",
        lambda: tmp_git_repo / "fake-agent-runner",
    )
    result = api.install(tmp_git_repo, system=False, with_monitor=False)
    assert result.unit_path.exists()
    assert result.monitor_unit_path is None


def test_given_install_with_monitor_when_called_then_writes_two_units(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    monkeypatch.setattr(
        "agent_runner.lifecycle._user_systemd_dir", lambda: tmp_git_repo / "fake-systemd"
    )
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


def test_given_installed_unit_when_uninstall_then_removes_file(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api.init(tmp_git_repo, force=False, commit=False)
    fake_systemd = tmp_git_repo / "fake-systemd"
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: fake_systemd)
    monkeypatch.setattr("agent_runner.api._systemctl_user", lambda *a: None)
    monkeypatch.setattr("agent_runner.api._check_user_systemd_available", lambda: None)
    monkeypatch.setattr(
        "agent_runner.api._agent_runner_script_path",
        lambda: tmp_git_repo / "fake-agent-runner",
    )
    api.install(tmp_git_repo, system=False, with_monitor=True)
    api.uninstall(tmp_git_repo)
    unit_name = f"agent-runner@{tmp_git_repo.name}.service"
    assert not (fake_systemd / unit_name).exists()


def test_given_per_phase_override_when_poll_once_then_forwards_phases_overrides_to_monitor(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_poll_once must forward cfg.phases.overrides to run_all_detectors as phases_overrides."""
    api.init(tmp_git_repo, force=False, commit=False)

    # Patch load_config to inject a phases override
    real_load = load_config

    def patched_load(path):
        cfg = real_load(path)
        import dataclasses

        return dataclasses.replace(
            cfg,
            phases=PhasesConfig(
                list=["dev"],
                overrides={"dev": PhaseOverride(round_timeout_s=3600)},
            ),
        )

    monkeypatch.setattr("agent_runner.api.load_config", patched_load)

    captured: list[dict] = []

    def capturing_rad(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr("agent_runner.monitor.run_all_detectors", capturing_rad)

    api._poll_once(tmp_git_repo, host=None)

    assert captured, "run_all_detectors was never called"
    call_kwargs = captured[0]
    assert "phases_overrides" in call_kwargs, (
        "phases_overrides kwarg missing from run_all_detectors call"
    )
    assert call_kwargs["phases_overrides"] == {"dev": PhaseOverride(round_timeout_s=3600)}


def test_poll_once_forwards_supervisor_stale_threshold(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_poll_once must forward cfg.monitor.supervisor_stale_threshold_s."""
    api.init(tmp_git_repo, force=False, commit=False)

    captured: list[dict] = []

    def capturing_rad(**kwargs):
        captured.append(kwargs)
        return []

    monkeypatch.setattr("agent_runner.monitor.run_all_detectors", capturing_rad)

    api._poll_once(tmp_git_repo, host=None)

    assert captured, "run_all_detectors was never called"
    call_kwargs = captured[0]
    assert "supervisor_stale_threshold_s" in call_kwargs
