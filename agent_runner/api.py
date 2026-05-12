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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_runner import lifecycle
from agent_runner.api_types import (
    InitResult,
    InstallResult,
    ProjectState,
    ServiceMode,
    ServiceStatus,
    select_path,
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
    """Return the configured log_dir from agent-runner.toml.

    Falls back to the conventional ~/.agent-runner/<project>/logs only when
    the toml is missing. This keeps `api.status` / `api.stop` aligned with
    where `serve_cmd.py` actually writes serve.pid.
    """
    cfg_path = work_dir / "agent-runner.toml"
    if cfg_path.exists():
        return load_config(cfg_path).runtime.log_dir
    return Path.home() / ".agent-runner" / _project_name(work_dir) / "logs"


def _venv_bin() -> Path:
    """Where this Python interpreter lives — for ExecStart."""
    return Path(sys.executable).parent


def _systemctl_user(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=False)


# ---------------------------------------------------------------------------
# init / install / uninstall


def init(work_dir: Path | None = None, *, force: bool = False, commit: bool = True) -> InitResult:
    if work_dir is None:
        work_dir = Path.cwd()
    return scaffold_project(work_dir, force=force, commit=commit)


def install(
    work_dir: Path | None = None, *, system: bool = False, with_monitor: bool = False
) -> InstallResult:
    if work_dir is None:
        work_dir = Path.cwd()
    if system:
        raise NotImplementedError("--system install not yet implemented in Phase 2")
    cfg_path = work_dir / "agent-runner.toml"
    cfg = load_config(cfg_path)
    project = _project_name(work_dir)

    units_dir = lifecycle._user_systemd_dir()
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

    return InstallResult(
        unit_path=serve_path, monitor_unit_path=monitor_path, enabled=True, started=True
    )


def uninstall(work_dir: Path | None = None) -> bool:
    if work_dir is None:
        work_dir = Path.cwd()
    project = _project_name(work_dir)
    units_dir = lifecycle._user_systemd_dir()
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
        unit = lifecycle._user_systemd_dir() / serve_unit_filename(pname)
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


# ---------------------------------------------------------------------------
# Observation: peek / monitor_loop / _poll_once
#
# Imported lazily to avoid pulling monitor + defenses at module load time
# for callers that only use lifecycle verbs.

from agent_runner import defenses, monitor  # noqa: E402


def peek(
    project: str | Path | None = None,
    *,
    round: int | str | None = None,
    log: bool = False,
    events: int | None = None,
    select: str | None = None,
) -> ProjectState | Any:
    """Build a ProjectState snapshot. With select, return that subtree."""
    from agent_runner import round_view

    work_dir = project if isinstance(project, Path) else Path.cwd()
    cfg = load_config(work_dir / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    src = monitor.LocalSource(log_dir=log_dir)
    base_state = monitor.assemble_project_state(src, project=_project_name(work_dir))
    parsed_events = monitor.parse_events_from_jsonl_files(src.events_files())
    round_num = round_view.resolve_round_arg(round, log_dir)
    current: Any = base_state.current_round
    if round_num is not None:
        current = round_view.build_round_view(log_dir, round_num, parsed_events, want_log=log)
        if current is None:
            raise KeyError(f"round {round_num} not found under {log_dir}/rounds/")
    recent = parsed_events[-events:] if events else []

    state = ProjectState(
        project=base_state.project,
        status=base_state.status,
        defenses=[
            {
                "name": d.name,
                "value": d.value,
                "codifies": d.codifies,
                "guarded_by": str(d.guarded_by) if d.guarded_by else None,
                "current_state": d.current_state,
            }
            for d in defenses.catalog(cfg)
        ],
        current_round=current,
        recent_rounds=base_state.recent_rounds,
        orphan=base_state.orphan,
        system=base_state.system,
        service=status(project if project is not None else work_dir),
        recent_events=recent,
    )
    return state if select is None else select_path(state, select)


def _poll_once(project: str | Path, *, host: str | None) -> list[monitor.Alert]:
    work_dir = project if isinstance(project, Path) else Path.cwd()
    cfg = load_config(work_dir / "agent-runner.toml")
    src: monitor.StateSource
    if host is None:
        src = monitor.LocalSource(log_dir=cfg.runtime.log_dir)
    else:
        src = monitor.RemoteSource(host=host, project=_project_name(work_dir))
    events = monitor.parse_events_from_jsonl_files(src.events_files())
    metrics = monitor.parse_events_from_jsonl_files(src.metrics_files())
    log_tails = monitor.load_round_log_tails(src.rounds_dir())
    builtin = monitor.run_all_detectors(
        events=events,
        metrics=metrics,
        log_tails=log_tails,
        round_timeout_s=cfg.runtime.round_timeout_s,
        auth_fail_patterns=cfg.monitor.auth_fail_patterns,
        auth_fail_hint=cfg.monitor.auth_fail_hint,
    )
    state = monitor.assemble_project_state(src, project=_project_name(work_dir))
    plugin = monitor.run_plugin_detectors(state)
    return builtin + plugin


def monitor_loop(
    project: str | Path | None = None, *, host: str | None = None, interval_s: int = 30
) -> Iterator[monitor.Alert]:
    """Yield alerts as they're detected. Caller decides what to do.

    The loop dedups alerts by (detector, json.dumps(context)) within session.
    """
    import json as _json

    seen: set[str] = set()
    work_dir = project if isinstance(project, Path) else Path.cwd()
    cfg = load_config(work_dir / "agent-runner.toml")
    while True:
        for alert in _poll_once(work_dir, host=host):
            key = f"{alert.detector}:{_json.dumps(alert.context, sort_keys=True)}"
            if key in seen:
                continue
            seen.add(key)
            yield alert
            monitor.on_alert(
                alert,
                project=_project_name(work_dir),
                host=host,
                log_dir=cfg.runtime.log_dir,
            )
        time.sleep(interval_s)
