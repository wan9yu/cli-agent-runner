"""Systemd install / uninstall: ``init``, ``install``, ``uninstall``.

Depends one-directionally on ``_lifecycle.py`` for the shared
``_project_name`` / ``_check_user_systemd_available`` / ``_SYSTEM_UNITS_DIR`` /
``_system_unit_exists`` / ``_PID_SIGNAL_GRACE_S`` helpers — never the reverse.

Re-exported from ``agent_runner.api`` (the thin facade) so external callers
and existing internal callers keep working unchanged.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: TID251 — install uses systemctl, subprocess
import sysconfig
from pathlib import Path

from agent_runner import _resolve, lifecycle
from agent_runner._lifecycle import (
    _PID_SIGNAL_GRACE_S,
    _SYSTEM_UNITS_DIR,
    _check_user_systemd_available,
    _project_name,
    _system_unit_exists,
)
from agent_runner.api_types import InitResult, InstallResult
from agent_runner.clock import SYSTEM_CLOCK
from agent_runner.config import Config, load_config
from agent_runner.lifecycle import _systemctl_user
from agent_runner.scaffold import scaffold_project
from agent_runner.service_unit import (
    monitor_unit_filename,
    render_monitor_unit,
    render_serve_unit,
    serve_unit_filename,
)


def _agent_runner_script_path() -> Path:
    """Locate the agent-runner CLI script for systemd ExecStart.

    Tries shutil.which first (honors PATH). Falls back to sysconfig's
    scripts dir (handles cases where PATH excludes the install dir).
    Raises FileNotFoundError if neither resolves to an existing file.
    """
    which = shutil.which("agent-runner")
    if which:
        return Path(which)
    scripts_dir = Path(sysconfig.get_path("scripts"))
    candidate = scripts_dir / "agent-runner"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        "agent-runner script not found in PATH or "
        f"{scripts_dir}; reinstall via pip or activate the right venv"
    )


def init(
    work_dir: Path | None = None,
    *,
    preset: str = "claude",
    force: bool = False,
    commit: bool = True,
) -> InitResult:
    if work_dir is None:
        work_dir = Path.cwd()
    return scaffold_project(work_dir, preset=preset, force=force, commit=commit)


def _install_system(
    cfg: Config, project: str, *, config_path: Path, with_monitor: bool
) -> InstallResult:
    if os.geteuid() != 0:
        raise RuntimeError(
            "--system requires sudo; run via `sudo -E agent-runner install --system`"
        )
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        raise RuntimeError(
            "--system needs SUDO_USER env var; run via "
            "`sudo -E agent-runner install --system` to preserve env"
        )
    script_path = _agent_runner_script_path()
    serve_path = _SYSTEM_UNITS_DIR / serve_unit_filename(project)
    serve_path.write_text(
        render_serve_unit(cfg, script_path=script_path, config_path=config_path, user=sudo_user)
    )
    monitor_path: Path | None = None
    if with_monitor:
        monitor_path = _SYSTEM_UNITS_DIR / monitor_unit_filename(project)
        monitor_path.write_text(
            render_monitor_unit(
                cfg, script_path=script_path, config_path=config_path, user=sudo_user
            )
        )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", serve_unit_filename(project)], check=True)
    if with_monitor:
        subprocess.run(["systemctl", "enable", monitor_unit_filename(project)], check=True)
    return InstallResult(
        unit_path=serve_path,
        monitor_unit_path=monitor_path,
        enabled=True,
        started=False,
    )


def install(
    work_dir: Path | None = None,
    *,
    system: bool = False,
    with_monitor: bool = False,
    force: bool = False,
) -> InstallResult:
    if work_dir is None:
        work_dir = Path.cwd()
    cfg_path = work_dir / "agent-runner.toml"
    cfg = load_config(cfg_path)
    project = _project_name(work_dir)

    units_dir = _SYSTEM_UNITS_DIR if system else lifecycle._user_systemd_dir()
    _resolve.guard_against_clobber(
        units_dir / serve_unit_filename(project), cfg.runtime.work_dir, force=force
    )

    if system:
        return _install_system(cfg, project, config_path=cfg_path, with_monitor=with_monitor)

    _check_user_systemd_available()
    script_path = _agent_runner_script_path()

    units_dir.mkdir(parents=True, exist_ok=True)

    serve_path = units_dir / serve_unit_filename(project)
    serve_path.write_text(render_serve_unit(cfg, script_path=script_path, config_path=cfg_path))

    monitor_path: Path | None = None
    if with_monitor:
        monitor_path = units_dir / monitor_unit_filename(project)
        monitor_path.write_text(
            render_monitor_unit(cfg, script_path=script_path, config_path=cfg_path)
        )

    _systemctl_user("daemon-reload")
    _systemctl_user("enable", serve_unit_filename(project))
    _systemctl_user("start", serve_unit_filename(project))
    if with_monitor:
        _systemctl_user("enable", monitor_unit_filename(project))
        _systemctl_user("start", monitor_unit_filename(project))

    return InstallResult(
        unit_path=serve_path, monitor_unit_path=monitor_path, enabled=True, started=True
    )


def uninstall(work_dir: Path | None = None) -> bool:
    if work_dir is None:
        work_dir = Path.cwd()
    project = _project_name(work_dir)
    if _system_unit_exists(project):  # root-owned -- don't silently no-op the user-scope dir
        units = [serve_unit_filename(project)]
        if (_SYSTEM_UNITS_DIR / monitor_unit_filename(project)).exists():
            units.append(monitor_unit_filename(project))
        remedy = " && ".join(
            f"sudo systemctl disable --now {u} && sudo rm {_SYSTEM_UNITS_DIR / u}" for u in units
        )
        print(f"{project} is managed by a system unit; run: {remedy}")
        return False
    units_dir = lifecycle._user_systemd_dir()
    serve = units_dir / serve_unit_filename(project)
    monitor = units_dir / monitor_unit_filename(project)
    for p in (serve, monitor):
        if p.exists():
            # Drain-aware (see stop_unit_draining, the same primitive api.stop/
            # api.restart use): a blocking `systemctl stop` would TimeoutExpired
            # while serve drains its round -- queue it and confirm within a
            # bounded window instead, same as stop(), before disabling/unlinking.
            lifecycle.stop_unit_draining(p.name, clock=SYSTEM_CLOCK, confirm_s=_PID_SIGNAL_GRACE_S)
            _systemctl_user("disable", p.name)
            p.unlink(missing_ok=True)
    try:
        _systemctl_user("daemon-reload")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # no user bus on this host -- units above (if any) already gone
    return True
