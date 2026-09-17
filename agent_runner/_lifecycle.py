"""Service lifecycle: start / stop / kill / restart / status.

Also holds the shared helpers ``_project_name`` / ``_log_dir`` / a project
reference resolver, and the systemd-availability probes, used by both
``_install.py`` (systemd install/uninstall) and ``_observe.py``
(peek/monitor). Both of those import from here, never the reverse.

Re-exported from ``agent_runner.api`` (the thin facade) so external callers
and existing internal callers keep working unchanged.
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: TID251 — lifecycle uses systemctl, subprocess
from pathlib import Path

from agent_runner import _resolve, lifecycle
from agent_runner._serve_policy import _ROUND_TERM_GRACE_S  # single source
from agent_runner.api_types import ServiceMode, ServiceStatus
from agent_runner.clock import SYSTEM_CLOCK, wait_until
from agent_runner.config import Config
from agent_runner.lifecycle import (
    _SYSTEMCTL_TIMEOUT_S,
    _SYSTEMD_ACTIVE_STATES,
    _SYSTEMD_INACTIVE_STATES,
    PIDFile,
    _systemctl_is_active,
    _systemctl_user,
    detect_service_mode,
    pid_alive,
    send_signal_to_pid,
)
from agent_runner.service_unit import serve_unit_filename


def outer_round_ceiling_s(cfg: Config, phase_arg: str | None) -> int:
    """Outer wall-clock ceiling for the round subprocess: the inner round timeout
    plus a DERIVED margin (agent reap grace + git-commit ceiling + hook allowance),
    so the ceiling only trips when the round supervisor itself is wedged, never
    while it does its own bounded post-round cleanup.

    Derived from ``_serve_policy.timeout_budget``, the single source shared
    with ``service_unit.py``'s ``TimeoutStopSec`` — kept as
    a local import so this stays out of api's re-exported public surface
    (internal-only, not a public contract).
    """
    from agent_runner._serve_policy import timeout_budget

    if phase_arg is not None:
        inner = cfg.profile_for(phase_arg).runtime.round_budget_s
    else:
        # rotation/legacy: any phase can override larger, so budget the max
        inner = max(
            (cfg.profile_for(p).runtime.round_budget_s for p in (cfg.phases.list or [])),
            default=cfg.runtime.round_budget_s,
        )
    _, outer_ceiling = timeout_budget(inner, goal_checks_allowance_s=cfg.goal_checks_allowance_s)
    return outer_ceiling


_LINGER_HINT = (
    "On headless distros, run `sudo loginctl enable-linger $USER` and "
    "re-login, OR pass `--system` for a system-level unit."
)


def _project_name(work_dir: Path) -> str:
    """Strict project name: api.py's lifecycle/observe verbs interpolate it
    into ssh remote commands and systemd unit filenames. See
    ``_resolve.project_name`` (single source, lenient/strict split)."""
    return _resolve.project_name(work_dir, strict=True)


def _log_dir(work_dir: Path) -> Path:
    """Return the configured log_dir. See ``_resolve.log_dir`` (single source):
    this keeps `api.status` / `api.stop` aligned with where `serve_cmd.py`
    actually writes serve.pid."""
    return _resolve.log_dir(work_dir)


def _check_user_systemd_available() -> None:
    """Raise RuntimeError if user systemd is not usable.

    Common on headless distros (dietpi, RPi OS Lite, Debian Server) without
    `loginctl enable-linger $USER`. Error includes remediation hint.
    """
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime_dir or not Path(runtime_dir).is_dir():
        raise RuntimeError(
            "user systemd unavailable (XDG_RUNTIME_DIR not set or missing). " + _LINGER_HINT
        )
    try:
        probe = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "systemctl binary not found in PATH; user systemd is not available. " + _LINGER_HINT
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"user systemd unavailable (systemctl did not respond within "
            f"{_SYSTEMCTL_TIMEOUT_S}s -- a wedged D-Bus session). " + _LINGER_HINT
        ) from exc
    if "Failed to connect to bus" in (probe.stderr or ""):
        raise RuntimeError("user systemd unavailable (D-Bus session not running). " + _LINGER_HINT)


def _systemd_active(unit_name: str, log_dir: Path) -> bool:
    """Map is-active state to liveness; the new StartLimit windows make
    `activating` a routine healthy state. An unknown state or absent systemctl
    falls back to serve.pid liveness."""
    state = _systemctl_is_active(unit_name)
    if state in _SYSTEMD_ACTIVE_STATES:
        return True
    if state in _SYSTEMD_INACTIVE_STATES:
        return False
    pid = PIDFile(log_dir / "serve.pid").read()
    return pid is not None and pid_alive(pid)


_SYSTEM_UNITS_DIR = Path("/etc/systemd/system")


def _system_unit_exists(project: str) -> bool:
    """A --system install's root-owned unit; detect_service_mode can't see it (user-scope only)."""
    return (_SYSTEM_UNITS_DIR / serve_unit_filename(project)).exists()


def start(project: str | Path) -> ServiceStatus:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.SYSTEMD_USER:
        _systemctl_user("start", serve_unit_filename(pname))
    return status(project)


# Bounded wait for a signaled PID_FILE-mode process to react before stop()/kill()
# report their outcome. Monotonic (not epoch): an NTP step must not stretch or
# skip the wait. Shared value: stop()'s confirm window and kill()'s pre-SIGKILL
# grace are the same bound, just followed by different escalation.
_PID_SIGNAL_GRACE_S = 5


def _await_pid_exit(pid: int, timeout_s: float) -> bool:
    """Poll ``pid_alive(pid)`` until it clears or ``timeout_s`` elapses.
    Returns the final liveness (True = still alive)."""
    exited = wait_until(SYSTEM_CLOCK, lambda: not pid_alive(pid), timeout_s=timeout_s)
    return not exited


def _round_holder_pid(log_dir: Path) -> int | None:
    """Read the live round-child pid from the round lock's ``.holder`` sidecar
    (``runner._write_holder_sidecar``), or None when no round is currently in
    flight, the sidecar is missing/corrupt, or the recorded pid is no longer
    alive. This is the ONLY way ``kill()`` can reach an in-flight round: the
    round is ``start_new_session=True`` (its own session, its own pgid), so it
    sits outside whatever process group serve itself belongs to."""
    from agent_runner.context_store import read_json

    data = read_json(log_dir / "agent-runner.lock.holder")
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if not lifecycle._valid_pid(pid):
        return None
    if not pid_alive(pid):
        return None
    recorded = data.get("create_time")
    return pid if lifecycle.create_time_matches(pid, recorded) else None


def _terminate_round_pid(pid: int) -> None:
    """TERM-first -> grace -> SIGKILL a bare round-child pid.

    Mirrors ``serve_cmd._terminate_round``'s shape, but this runs from a
    SEPARATE CLI process (``kill()``) that only has the pid -- not serve's own
    ``Popen`` handle. Never ``killpg``: the round is ``start_new_session=True``,
    so a plain SIGTERM here fires the round's own SIGTERM -> KeyboardInterrupt
    handler (``round_cmd.py``), which walks the link to the agent pgroup and
    reaps it via ``_kill_pgroup`` (itself snapshotting and reaping any stray).
    SIGKILL is only the last-resort escalation for a round that never even runs
    its handler -- and on THAT path the leader's own cooperative reap never
    happened, so we snapshot the leader's descendants up front (while the leader
    subtree is still resolvable) via a bare-pid shim and reap the strays here:
    a descendant that ``setsid()``'d off the leader's pgroup would otherwise
    survive the leader's death, orphaned. Symmetric with ``serve stop``."""
    import types

    from agent_runner import agent_runtime

    stray = agent_runtime._snapshot_stray_descendants(types.SimpleNamespace(pid=pid))
    send_signal_to_pid(pid, signal.SIGTERM)
    if _await_pid_exit(pid, _ROUND_TERM_GRACE_S):
        send_signal_to_pid(pid, signal.SIGKILL)
        agent_runtime._kill_stray_descendants(stray)


def stop(project: str | Path) -> ServiceStatus:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.SYSTEMD_USER:
        # Drain-aware (see stop_unit_draining): a blocking `systemctl stop` would
        # TimeoutExpired while serve drains its round, so queue it and confirm
        # within the same bounded window the PID_FILE path uses. A still-draining
        # unit reports active=True via status.
        lifecycle.stop_unit_draining(
            serve_unit_filename(pname), clock=SYSTEM_CLOCK, confirm_s=_PID_SIGNAL_GRACE_S
        )
        return status(project)
    pid = PIDFile(log_dir / "serve.pid").read()
    if pid is not None:
        send_signal_to_pid(pid, signal.SIGTERM)
        # Confirm within a bounded window so a caller (monitor.on_alert) sees
        # active=False for a graceful stop that actually took — otherwise
        # every synchronous check would race the process's own shutdown and
        # (correctly, but uselessly) report active=True every time.
        _await_pid_exit(pid, _PID_SIGNAL_GRACE_S)
    return status(project)


def kill(project: str | Path) -> ServiceStatus:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.SYSTEMD_USER:
        unit = serve_unit_filename(pname)
        _systemctl_user("kill", "--signal=SIGTERM", unit)
        # Match the PID_FILE branch's escalation (and the docs' "grace then
        # SIGKILL"): `systemctl kill` queues no stop job, so TimeoutStopSec
        # never applies -- a unit still active after the grace window needs
        # an explicit SIGKILL. Bounded-poll via wait_until, same shape as
        # _await_pid_exit / stop_unit_draining, clock-injected.
        inactive = wait_until(
            SYSTEM_CLOCK,
            lambda: _systemctl_is_active(unit) in _SYSTEMD_INACTIVE_STATES,
            timeout_s=_PID_SIGNAL_GRACE_S,
        )
        if not inactive:
            _systemctl_user("kill", "--signal=SIGKILL", unit)
        return status(project)
    pid = PIDFile(log_dir / "serve.pid").read()
    if pid is None:
        return status(project)
    # SIGTERM serve FIRST so its own graceful handler arms stop["requested"]
    # before we forcibly end the in-flight round below. Otherwise serve's loop
    # could see the round we are about to kill exit and spawn a NEW one before
    # our own SIGTERM to serve has landed.
    send_signal_to_pid(pid, signal.SIGTERM)
    round_pid = _round_holder_pid(log_dir)
    if round_pid is not None:
        _terminate_round_pid(round_pid)
    alive = _await_pid_exit(pid, _PID_SIGNAL_GRACE_S)
    if alive:
        send_signal_to_pid(pid, signal.SIGKILL)
        alive = pid_alive(pid)  # re-check: SIGKILL may have reaped it
    return ServiceStatus(mode=ServiceMode.PID_FILE, active=alive, pid=pid)


def restart(project: str | Path, *, force: bool = False) -> ServiceStatus:
    # Detect mode FIRST and refuse before stop()/kill(): start() only respawns a
    # SYSTEMD_USER unit, so restarting a PID_FILE/NONE service would stop it and
    # never bring it back — the half-execution this fix eliminates.
    pname = _resolve_project(project)
    if _system_unit_exists(pname):  # root-owned -- below's message would mislead
        raise RuntimeError(
            f"{pname} is managed by a system unit; run: "
            f"sudo systemctl restart {serve_unit_filename(pname)}"
        )
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode != ServiceMode.SYSTEMD_USER:
        raise RuntimeError(
            f"restart requires a systemd-user service; {pname} is {mode.value}. "
            "A plain serve process cannot be respawned by the CLI — stop/kill it "
            "and start it again by hand."
        )
    if force:
        kill(project)
    else:
        stop(project)  # drain-aware: returns without raising; may still be draining
    # A blocking `systemctl start` queued behind an in-flight drain would itself
    # TimeoutExpired, leaving the service stopped — the half-execution restart must
    # never produce. stop()/kill() already polled a bounded confirm window, so one
    # is-active check decides: fully stopped -> confirming blocking start(); still
    # draining -> queue the start so systemd brings it back after the drain.
    unit = serve_unit_filename(pname)
    if _systemctl_is_active(unit) in _SYSTEMD_INACTIVE_STATES:
        return start(project)
    _systemctl_user("--no-block", "start", unit)
    return status(project)


def status(project: str | Path) -> ServiceStatus:
    pname = _resolve_project(project)
    log_dir = _log_dir_for_project(project)
    mode = detect_service_mode(pname, log_dir=log_dir)
    if mode == ServiceMode.SYSTEMD_USER:
        unit = lifecycle._user_systemd_dir() / serve_unit_filename(pname)
        active = _systemd_active(serve_unit_filename(pname), log_dir)
        return ServiceStatus(mode=mode, active=active, unit_file=unit)
    # detect_service_mode only sees the user-scope unit dir; label a --system
    # install found here (PID_FILE/NONE) via system_managed, not unit_file.
    system_managed = _system_unit_exists(pname)
    if mode == ServiceMode.PID_FILE:
        pid = PIDFile(log_dir / "serve.pid").read()
        active = pid is not None and pid_alive(pid)
        return ServiceStatus(mode=mode, active=active, pid=pid, system_managed=system_managed)
    return ServiceStatus(mode=ServiceMode.NONE, active=False, system_managed=system_managed)


def _resolve_target(project: str | Path | None) -> tuple[str, Path]:
    """Resolve a project reference to (project_name, log_dir), Path-first.

    ONE resolution feeds both the name and the log_dir so peek/status can never
    resolve them against different projects (the mixed-project peek bug). A Path
    (or None -> cwd) reads log_dir from that directory's toml; a path-like or
    bare string is validated against _PROJECT_NAME_RE before use.
    """
    if project is None:
        project = Path.cwd()
    if isinstance(project, Path):
        return _project_name(project), _log_dir(project)
    if "/" in project or "\\" in project:
        p = Path(project)
        return _project_name(p), _log_dir(p)
    if not _resolve._PROJECT_NAME_RE.match(project):
        raise ValueError(f"invalid project name {project!r}: must match [A-Za-z0-9._-]+")
    if project == _project_name(Path.cwd()):
        return project, _log_dir(Path.cwd())
    return project, _resolve.default_log_dir(project)


def _resolve_project(project: str | Path) -> str:
    return _resolve_target(project)[0]


def _log_dir_for_project(project: str | Path) -> Path:
    return _resolve_target(project)[1]
