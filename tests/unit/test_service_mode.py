"""detect_service_mode precedence — spec-review correction: serve writes serve.pid in
BOTH modes (serve_cmd.py), so a plain "pidfile exists" check cannot tell a
healthy systemd serve from a hand-launched one. Precedence must be gated on
`systemctl --user is-active`, not on pidfile-vs-unit-file existence, or
`restart` (which requires SYSTEMD_USER) breaks against a perfectly healthy
systemd-managed serve."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_runner.api_types import ServiceMode
from agent_runner.lifecycle import detect_service_mode


def _install_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project: str) -> Path:
    fake_systemd = tmp_path / "systemd-user"
    fake_systemd.mkdir(exist_ok=True)
    (fake_systemd / f"agent-runner@{project}.service").write_text("[Unit]\n")
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: fake_systemd)
    return fake_systemd


def test_detect_service_mode_should_stay_systemd_user_when_serve_is_healthy_under_systemd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The naive fix (pidfile ranked above the unit) would misroute this to
    PID_FILE and break `restart` — serve writes serve.pid in BOTH modes, so a
    live pidfile next to an ACTIVE unit must still resolve to SYSTEMD_USER."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "serve.pid").write_text(str(os.getpid()))
    _install_unit(tmp_path, monkeypatch, "myproj")
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_is_active", lambda u: "active")

    mode = detect_service_mode("myproj", log_dir=log_dir)

    assert mode == ServiceMode.SYSTEMD_USER


def test_detect_service_mode_should_return_pid_file_when_hand_launched_with_no_unit(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "serve.pid").write_text(str(os.getpid()))

    mode = detect_service_mode("proj", log_dir=log_dir)

    assert mode == ServiceMode.PID_FILE


def test_detect_service_mode_should_return_none_when_no_unit_and_no_pidfile(
    tmp_path: Path,
) -> None:
    mode = detect_service_mode("nonexistent-project", log_dir=tmp_path)

    assert mode == ServiceMode.NONE


def test_detect_service_mode_should_return_pid_file_when_unit_failed_but_pidfile_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unit is installed but its systemd record is stale/failed, while a
    plain serve process is ACTUALLY running (a live serve.pid) -- signal what
    is really alive, not the dead unit."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "serve.pid").write_text(str(os.getpid()))
    _install_unit(tmp_path, monkeypatch, "myproj")
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_is_active", lambda u: "failed")

    mode = detect_service_mode("myproj", log_dir=log_dir)

    assert mode == ServiceMode.PID_FILE


def test_detect_service_mode_should_return_systemd_user_when_unit_inactive_and_no_live_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is running at all: `start` must still resolve to SYSTEMD_USER so
    it can respawn the unit -- PID_FILE here would have nothing to signal."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _install_unit(tmp_path, monkeypatch, "myproj")
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_is_active", lambda u: "inactive")

    mode = detect_service_mode("myproj", log_dir=log_dir)

    assert mode == ServiceMode.SYSTEMD_USER


def test_detect_service_mode_should_return_systemd_user_when_pidfile_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead pid left behind in serve.pid (crash without cleanup) must not be
    treated as "something is really alive" -- falls through to SYSTEMD_USER."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "serve.pid").write_text("999999999")
    _install_unit(tmp_path, monkeypatch, "myproj")
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_is_active", lambda u: "inactive")

    mode = detect_service_mode("myproj", log_dir=log_dir)

    assert mode == ServiceMode.SYSTEMD_USER


def test_detect_service_mode_should_fall_back_to_pid_file_when_systemctl_probe_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No systemctl binary (darwin dev box, container) -> the probe returns
    None, which must be treated as "not active" (same fallback as
    inactive/failed), not crash and not silently claim SYSTEMD_USER over a
    live plain process."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "serve.pid").write_text(str(os.getpid()))
    _install_unit(tmp_path, monkeypatch, "myproj")
    monkeypatch.setattr("agent_runner.lifecycle._systemctl_is_active", lambda u: None)

    mode = detect_service_mode("myproj", log_dir=log_dir)

    assert mode == ServiceMode.PID_FILE
