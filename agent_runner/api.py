"""Public Python API mirroring CLI verbs.

Every CLI subcommand has a corresponding api function. CLI files do
``api.X(...)`` and format the returned dataclass for display. External
agents (Phase 3 outer Claude Code) can `from agent_runner import api`
and skip CLI text parsing entirely.
"""

from __future__ import annotations

import signal
import subprocess  # noqa: TID251 — api uses systemctl + ssh, both subprocess
import sys
import time
from pathlib import Path

from agent_runner.api_types import (
    InitResult,
    InstallResult,
    ServiceMode,
    ServiceStatus,
)
from agent_runner.config import load_config
from agent_runner.lifecycle import (
    PIDFile,
    detect_service_mode,
    pid_alive,
    send_signal_to_pid,
)
from agent_runner.scaffold import scaffold_project
from agent_runner.service_unit import (
    monitor_unit_filename,
    render_monitor_unit,
    render_serve_unit,
    serve_unit_filename,
)


def _project_name(work_dir: Path) -> str:
    return work_dir.resolve().name or "default"


def _log_dir(work_dir: Path) -> Path:
    return work_dir / "logs"


def _user_systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _venv_bin() -> Path:
    """Where this Python interpreter lives — for ExecStart."""
    return Path(sys.executable).parent


def _systemctl_user(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=False)


# ---------------------------------------------------------------------------
# init / install / uninstall

def init(work_dir: Path | None = None, *, force: bool = False,
         commit: bool = True) -> InitResult:
    if work_dir is None:
        work_dir = Path.cwd()
    return scaffold_project(work_dir, force=force, commit=commit)


def install(work_dir: Path | None = None, *, system: bool = False,
            with_monitor: bool = False) -> InstallResult:
    if work_dir is None:
        work_dir = Path.cwd()
    if system:
        raise NotImplementedError("--system install not yet implemented in Phase 2")
    cfg_path = work_dir / "agent-runner.toml"
    cfg = load_config(cfg_path)
    project = _project_name(work_dir)

    units_dir = _user_systemd_dir()
    units_dir.mkdir(parents=True, exist_ok=True)

    serve_path = units_dir / serve_unit_filename(project)
    serve_path.write_text(render_serve_unit(cfg, venv_bin=_venv_bin()))

    monitor_path: Path | None = None
    if with_monitor:
        monitor_path = units_dir / monitor_unit_filename(project)
        monitor_path.write_text(render_monitor_unit(cfg, venv_bin=_venv_bin()))

    _systemctl_user("daemon-reload")
    _systemctl_user("enable", serve_unit_filename(project))
    _systemctl_user("start", serve_unit_filename(project))
    if with_monitor:
        _systemctl_user("enable", monitor_unit_filename(project))
        _systemctl_user("start", monitor_unit_filename(project))

    return InstallResult(unit_path=serve_path, monitor_unit_path=monitor_path,
                         enabled=True, started=True)


def uninstall(work_dir: Path | None = None) -> bool:
    if work_dir is None:
        work_dir = Path.cwd()
    project = _project_name(work_dir)
    units_dir = _user_systemd_dir()
    serve = units_dir / serve_unit_filename(project)
    monitor = units_dir / monitor_unit_filename(project)
    for p in (serve, monitor):
        if p.exists():
            _systemctl_user("stop", p.name)
            _systemctl_user("disable", p.name)
            p.unlink(missing_ok=True)
    _systemctl_user("daemon-reload")
    return True


# ---------------------------------------------------------------------------
# Lifecycle: start / stop / kill / cancel / restart / status

def start(project: str | Path) -> ServiceStatus:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.SYSTEMD_USER:
        _systemctl_user("start", serve_unit_filename(pname))
    return status(project)


def stop(project: str | Path) -> ServiceStatus:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.SYSTEMD_USER:
        _systemctl_user("stop", serve_unit_filename(pname))
        return status(project)
    pid = PIDFile(log_dir / "serve.pid").read()
    if pid is not None:
        send_signal_to_pid(pid, signal.SIGTERM)
    return status(project)


def kill(project: str | Path) -> ServiceStatus:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.SYSTEMD_USER:
        _systemctl_user("kill", "--signal=SIGTERM", serve_unit_filename(pname))
        return status(project)
    pid = PIDFile(log_dir / "serve.pid").read()
    if pid is None:
        return status(project)
    send_signal_to_pid(pid, signal.SIGTERM)
    deadline = time.time() + 5
    alive = True
    while time.time() < deadline:
        alive = pid_alive(pid)
        if not alive:
            break
        time.sleep(0.1)
    if alive:
        send_signal_to_pid(pid, signal.SIGKILL)
    return ServiceStatus(mode=ServiceMode.PID_FILE, active=alive, pid=pid)


def cancel(project: str | Path) -> bool:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.SYSTEMD_USER:
        _systemctl_user("kill", "--signal=SIGUSR1", serve_unit_filename(pname))
        return True
    pid = PIDFile(log_dir / "serve.pid").read()
    if pid is None:
        return False
    return send_signal_to_pid(pid, signal.SIGUSR1)


def restart(project: str | Path, *, force: bool = False) -> ServiceStatus:
    if force:
        kill(project)
    else:
        stop(project)
    return start(project)


def status(project: str | Path) -> ServiceStatus:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.PID_FILE:
        pid = PIDFile(log_dir / "serve.pid").read()
        return ServiceStatus(mode=mode, active=pid is not None and pid_alive(pid), pid=pid)
    if mode == ServiceMode.SYSTEMD_USER:
        unit = _user_systemd_dir() / serve_unit_filename(pname)
        return ServiceStatus(mode=mode, active=True, unit_file=unit)
    return ServiceStatus(mode=ServiceMode.NONE, active=False)


def _resolve_project(project: str | Path) -> str:
    if isinstance(project, Path):
        return _project_name(project)
    if "/" in project or "\\" in project:
        return _project_name(Path(project))
    return project


def _log_dir_for_project(project: str | Path) -> Path:
    if isinstance(project, Path):
        return _log_dir(project)
    p = Path.cwd() if project == _project_name(Path.cwd()) else None
    if p is not None:
        return _log_dir(p)
    return Path.home() / ".agent-runner" / project / "logs"
