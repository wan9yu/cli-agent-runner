"""Public Python API mirroring CLI verbs.

Every CLI subcommand has a corresponding api function. CLI files do
``api.X(...)`` and format the returned dataclass for display. External
callers can ``from agent_runner import api`` and skip CLI text parsing
entirely.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import signal
import subprocess  # noqa: TID251 — api uses systemctl + ssh, both subprocess
import sysconfig
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, TextIO

from agent_runner import _resolve, events, lifecycle

# Restart policy lives in _serve_policy (pure, dependency-free). Re-exported
# here so external callers keep `from agent_runner.api import post_round_decision`.
from agent_runner._serve_policy import (  # noqa: F401 — public re-export
    CRASH_LOOP_EXIT,
    CRASH_LOOP_MAX_DELAY_S,
    CRASH_LOOP_SHORT_EXIT_S,
    CRASH_LOOP_THRESHOLD,
    ENV_BATTERY_EXIT,
    MEM_LOOP_EXIT,
    MEM_LOOP_PERSISTENT_EXIT,
    PERMANENT_CONFIG_EXIT,
    post_round_decision,
)
from agent_runner.api_types import (
    InitResult,
    InstallResult,
    ProjectState,
    RateLimitState,
    ServiceMode,
    ServiceStatus,
    select_path,
)
from agent_runner.clock import SYSTEM_CLOCK, wait_until
from agent_runner.config import Config, load_config
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
from agent_runner.scaffold import scaffold_project
from agent_runner.service_unit import (
    monitor_unit_filename,
    render_monitor_unit,
    render_serve_unit,
    serve_unit_filename,
)


def outer_round_ceiling_s(cfg: Config, phase_arg: str | None) -> int:
    """Outer wall-clock ceiling for the round subprocess: the inner round timeout
    plus a DERIVED margin (agent reap grace + git-commit ceiling + hook allowance),
    so the ceiling only trips when the round supervisor itself is wedged, never
    while it does its own bounded post-round cleanup.

    Derived from ``_serve_policy.timeout_budget``, the single source shared
    with ``service_unit.py``'s ``TimeoutStopSec`` (Group C, seam 3) — kept as
    a local import so this stays out of api's re-exported public surface
    (internal-only, not a 0.2.13 public contract).
    """
    from agent_runner._serve_policy import timeout_budget

    if phase_arg is not None:
        inner = cfg.profile_for(phase_arg).runtime.round_timeout_s
    else:
        # rotation/legacy: any phase can override larger, so budget the max
        inner = max(
            (cfg.profile_for(p).runtime.round_timeout_s for p in (cfg.phases.list or [])),
            default=cfg.runtime.round_timeout_s,
        )
    _, outer_ceiling = timeout_budget(inner)
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


# ---------------------------------------------------------------------------
# init / install / uninstall


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


_SYSTEM_UNITS_DIR = Path("/etc/systemd/system")


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
    _systemctl_user("daemon-reload")
    return True


# ---------------------------------------------------------------------------
# Lifecycle: start / stop / kill / restart / status


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

# Grace after TERMing the round-lock holder before escalating to SIGKILL: mirrors
# serve_cmd._ROUND_TERM_GRACE_S (the round's own SIGTERM handler needs
# agent_runtime.REAP_GRACE_S + margin to reap its agent pgroup and exit).
# Cross-checked against serve_cmd's value by
# test_round_kill_grace_matches_serve so the two never drift.
_ROUND_TERM_GRACE_S = 15


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
    return pid if pid_alive(pid) else None


def _terminate_round_pid(pid: int) -> None:
    """TERM-first -> grace -> SIGKILL a bare round-child pid.

    Mirrors ``serve_cmd._terminate_round``'s shape, but this runs from a
    SEPARATE CLI process (``kill()``) that only has the pid — not serve's own
    ``Popen`` handle. Never ``killpg``: the round is ``start_new_session=True``,
    so its own pgid holds only itself, and even a killpg on it cannot reach the
    agent (itself a separate session again, ``agent_runtime.py``) — only the
    round's own SIGTERM -> KeyboardInterrupt handler (``round_cmd.py``) walks
    that link and reaps the agent pgroup via ``_kill_pgroup``. A plain SIGTERM
    here fires exactly that handler; SIGKILL is only the last-resort escalation
    for a round that doesn't even get to run its handler."""
    send_signal_to_pid(pid, signal.SIGTERM)
    if _await_pid_exit(pid, _ROUND_TERM_GRACE_S):
        send_signal_to_pid(pid, signal.SIGKILL)


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
        _systemctl_user("kill", "--signal=SIGTERM", serve_unit_filename(pname))
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
    if mode == ServiceMode.PID_FILE:
        pid = PIDFile(log_dir / "serve.pid").read()
        return ServiceStatus(mode=mode, active=pid is not None and pid_alive(pid), pid=pid)
    if mode == ServiceMode.SYSTEMD_USER:
        unit = lifecycle._user_systemd_dir() / serve_unit_filename(pname)
        active = _systemd_active(serve_unit_filename(pname), log_dir)
        return ServiceStatus(mode=mode, active=active, unit_file=unit)
    return ServiceStatus(mode=ServiceMode.NONE, active=False)


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


# ---------------------------------------------------------------------------
# Observation: peek / monitor_loop / _poll_once
#
# Imported lazily to avoid pulling monitor + defenses at module load time
# for callers that only use lifecycle verbs.

from agent_runner import defenses, monitor  # noqa: E402
from agent_runner.events import (  # noqa: E402
    AGENT_NETWORK_BLIP,
    HOOK_FAILED,
    MONITOR_STARTED,
)

_RECENT_HOOK_FAILURES_LIMIT = 10
_RECENT_BLIPS_LIMIT = 5
_MONITOR_SEEN_CAP = 512
"""Bound on `_monitor_loop_iter`'s dedup set — an unbounded set of alert-identity
keys would grow forever across a long-lived monitor process; oldest-episode
eviction (OrderedDict.popitem(last=False)) keeps memory flat."""


def _recent_events_of_kind(
    parsed_events: list[dict[str, Any]], kind: str, limit: int
) -> list[dict[str, Any]]:
    """Return the last ``limit`` events matching ``kind``, in chronological order.

    Walks the event list in reverse so we stop as soon as the limit is filled —
    parsed_events grows unboundedly over a project's lifetime; a full-scan
    comprehension here would dominate watch-loop peek cost.
    """
    out: list[dict[str, Any]] = []
    for e in reversed(parsed_events):
        if e.get("event") == kind:
            out.append(e)
            if len(out) == limit:
                break
    out.reverse()
    return out


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
    recent_hook_failures = _recent_events_of_kind(
        parsed_events, HOOK_FAILED, _RECENT_HOOK_FAILURES_LIMIT
    )
    recent_blips = _recent_events_of_kind(parsed_events, AGENT_NETWORK_BLIP, _RECENT_BLIPS_LIMIT)

    from agent_runner._throttle import effective_throttle_view

    throttle, active = effective_throttle_view(log_dir)
    rate_limit: RateLimitState | None = None
    if throttle is not None:
        rate_limit = RateLimitState(
            throttled_until_epoch=throttle.reset_at_epoch,
            limit_type=throttle.classification,
            agent=throttle.agent,
            since_round=throttle.since_round,
            phase=throttle.phase,
            throttled_agents=tuple(sorted(active)),
        )
    # Resolve service state from the SAME project the events came from: work_dir
    # is the Path peek loaded cfg/log_dir from (a bare name falls back to cwd),
    # so status() can't drift to a sibling project's serve.pid.
    raw_service = status(work_dir)
    svc = dataclasses.replace(raw_service, rate_limit=rate_limit)

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
        service=svc,
        recent_events=recent,
        recent_hook_failures=recent_hook_failures,
        recent_blips=recent_blips,
        schedule=monitor.latest_schedule_state(parsed_events),
    )
    return state if select is None else select_path(state, select)


def _poll_once(
    project: str | Path, *, event_tail: monitor._EventTail | None = None
) -> list[monitor.Alert]:
    work_dir = project if isinstance(project, Path) else Path.cwd()
    cfg = load_config(work_dir / "agent-runner.toml")
    # Always local: detection runs on the supervised host by design. Remote
    # observation is an event RELAY (``monitor --host X --mode events``), not a
    # remote poll — see agent_runner/remote_relay.py.
    src: monitor.StateSource = monitor.LocalSource(log_dir=cfg.runtime.log_dir)
    if event_tail is not None:
        events = event_tail.read(src.events_files())
    else:
        events = monitor.parse_events_from_jsonl_files(src.events_files())
    metrics = monitor.parse_events_from_jsonl_files(src.metrics_files())
    log_tails = monitor.load_round_log_tails(src.rounds_dir())
    builtin = monitor.run_all_detectors(
        events=events,
        metrics=metrics,
        log_tails=log_tails,
        round_timeout_s=cfg.runtime.round_timeout_s,
        supervisor_stale_threshold_s=cfg.monitor.supervisor_stale_threshold_s,
        auth_fail_patterns=cfg.monitor.auth_fail_patterns,
        auth_fail_hint=cfg.monitor.auth_fail_hint,
        phases_overrides=cfg.phases.overrides if cfg.phases.overrides else None,
        mem_avail_min_mb=cfg.monitor.host_health.mem_avail_min_mb,
        disk_warning_pct=cfg.monitor.host_health.disk_warning_pct,
        disk_critical_pct=cfg.monitor.host_health.disk_critical_pct,
        swap_sout_noise_floor_mb=cfg.monitor.host_health.swap_sout_noise_floor_mb,
        mem_free_low_mb=cfg.monitor.host_health.mem_free_low_mb,
        log_dir=cfg.runtime.log_dir,
    )
    if not monitor._PLUGIN_DETECTORS:
        return builtin  # skip ProjectState assembly when no plugins to feed
    state = monitor.assemble_project_state(src, project=_project_name(work_dir))
    plugin = monitor.run_plugin_detectors(state)
    return builtin + plugin


def monitor_loop(
    project: str | Path | None = None, *, host: str | None = None, interval_s: int = 30
) -> Iterator[monitor.Alert]:
    """Yield alerts as they're detected. Caller decides what to do.

    The loop dedups alerts by ``monitor.alert_identity`` (a stable per-episode key,
    not the raw measurement) within a bounded, oldest-evicted window so a single
    long-running alert cannot spam and the dedup set cannot grow unbounded.
    Emits ``monitor_started`` once at entry — programmatic consumers can subscribe
    to that kind as the canonical "supervision is up" signal (monitor is otherwise
    silent during healthy operation by design).

    ``host`` raises ``MonitorRemoteUnsupportedError`` immediately: detection runs
    on the supervised host by design, so there is no remote polling mode. The
    check is eager — this wrapper validates before handing back the generator,
    so the failure lands at startup rather than at the first ``next()``. For a
    remote event stream, see ``relay_remote_events``.
    """
    if host is not None:
        raise monitor.MonitorRemoteUnsupportedError(host)
    return _monitor_loop_iter(project, host=host, interval_s=interval_s)


def _monitor_loop_iter(
    project: str | Path | None = None, *, host: str | None = None, interval_s: int = 30
) -> Iterator[monitor.Alert]:
    """Polling generator behind ``monitor_loop``.

    ``host`` is None by construction (``monitor_loop`` rejects anything else);
    it is carried into the ``monitor_started`` payload as an explicit record
    that this monitor watches its own host.
    """
    import warnings
    from collections import OrderedDict

    seen: OrderedDict[str, None] = OrderedDict()
    work_dir = project if isinstance(project, Path) else Path.cwd()
    cfg = load_config(work_dir / "agent-runner.toml")
    cfg.runtime.log_dir.mkdir(parents=True, exist_ok=True)
    events.emit(
        cfg.runtime.log_dir,
        MONITOR_STARTED,
        host=host,
        interval_s=interval_s,
        log_dir=str(cfg.runtime.log_dir),
        mode="anomaly-only",
    )

    event_tail = monitor._EventTail()
    while True:
        try:
            alerts = _poll_once(work_dir, event_tail=event_tail)
        except Exception as e:  # noqa: BLE001 — a poll crash must not kill supervision
            warnings.warn(f"monitor poll failed: {type(e).__name__}: {e}", stacklevel=2)
            SYSTEM_CLOCK.sleep(interval_s)
            continue
        for alert in alerts:
            key = monitor.alert_identity(alert)
            if key in seen:
                seen.move_to_end(key)
                continue
            seen[key] = None
            if len(seen) > _MONITOR_SEEN_CAP:
                seen.popitem(last=False)  # bounded: evict oldest episode
            yield alert
            # Pass the work_dir Path (not the bare project name): api.stop resolves
            # a name's log_dir cwd-dependently, so a monitor launched from a cwd !=
            # work_dir with a non-preset log_dir would target the wrong dir, see no
            # pidfile, and no-op while serve keeps running. The Path resolves to the
            # real cfg.runtime.log_dir.
            monitor.on_alert(
                alert,
                project=work_dir,
                log_dir=cfg.runtime.log_dir,
                allowed_stop_names=cfg.monitor.auto_stop_on,
            )
        # Re-arm: an episode absent from this poll has cleared, so forget it — a
        # later recurrence is a NEW episode and must fire again (not stay suppressed
        # until bounded eviction). Only keys still firing this poll survive.
        current = {monitor.alert_identity(a) for a in alerts}
        for key in list(seen):
            if key not in current:
                del seen[key]
        SYSTEM_CLOCK.sleep(interval_s)


def _tail_events_jsonl(
    log_dir: Path,
    *,
    start_at_now: bool,
    poll_interval_s: float,
) -> Iterator[dict[str, Any]]:
    """Polling tailer: yields parsed event dicts from events-*.jsonl files.

    ``start_at_now``: if True, snapshot current file sizes at init so existing
    events are skipped (machine-consumption use case). If False, yield from
    byte 0 of every file present at start (human-narrate use case).

    Follows file rotation transparently — when a new events-YYYY-MM.jsonl
    appears, it is picked up from byte 0.
    """
    from agent_runner.events import _iter_parsed_lines, open_events_jsonl

    seen_positions: dict[Path, int] = {}
    if start_at_now:
        for path in sorted(log_dir.glob("events-*.jsonl")):
            try:
                seen_positions[path] = path.stat().st_size
            except FileNotFoundError:
                continue

    while True:
        files = sorted(log_dir.glob("events-*.jsonl"))
        any_new = False
        for path in files:
            pos = seen_positions.get(path, 0)
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            if size <= pos:
                continue
            with open_events_jsonl(path) as f:
                f.seek(pos)
                # narrate_events -> _format_narrate_line does evt.get(...) --
                # a non-dict line must not reach it or stream_events_jsonl's
                # machine-consumption callers.
                for _, evt in _iter_parsed_lines(f):
                    yield evt
                    any_new = True
                seen_positions[path] = f.tell()
        if not any_new:
            SYSTEM_CLOCK.sleep(poll_interval_s)


# Re-export emit_* wrappers from _emit module (extracted for size hygiene).
# Preserves the public import surface: `from agent_runner.api import emit_*` continues to work.
from agent_runner._emit import (  # noqa: E402,F401 — intentional bottom re-export
    emit_agent_auth_error_detected,
    emit_agent_usage_recorded,
    emit_anomaly_repetitive_tool,
    emit_config_broken,
    emit_config_migrated,
    emit_crash_loop,
    emit_fresh_eyes_round_triggered,
    emit_host_cgroup_memory_limit,
    emit_max_rounds_reached,
    emit_mem_loop,
    emit_mem_loop_persistent,
    emit_mem_pressure_deferred_to_cgroup,
    emit_rate_limit_stop,
    emit_round_deferred,
    emit_round_grace_extended,
    emit_round_grace_kill,
    emit_round_logs_prune_deferred,
    emit_round_mem_critical_sample,
    emit_round_mem_terminated,
    emit_round_progress,
    emit_round_resumed,
    emit_round_substrate_after,
    emit_round_substrate_before,
    emit_round_supervisor_wedged,
    emit_schedule_paused,
    emit_schedule_phase_skipped,
    emit_schedule_resumed,
    emit_stale_index_lock_cleared,
    emit_stop_file_detected,
    emit_transient_error_backoff_capped,
    emit_transient_error_detected,
    emit_transient_error_recovered,
)

# Round-input assembly + sentinel helpers live in _round_support (extracted
# for size hygiene, 0.2.14 Group 4). Re-exported here so `from agent_runner.api
# import assemble_prompt` etc. continue to work -- RuntimeConfig travels along
# with resolve_runtime_for_phase since it's the return type plugin authors
# need to annotate against, and it's itself pinned in EXPECTED_API_SURFACE.
from agent_runner._round_support import (  # noqa: E402,F401 — intentional bottom re-export
    RuntimeConfig,
    assemble_prompt,
    check_self_terminated_sentinel,
    read_round_num,
    read_sentinel_content,
    resolve_runtime_for_phase,
)

# Re-export git primitives so external callers can reach them via the api facade.
# The exception types travel with their functions: a caller catching what
# stash_orphan / try_auto_commit raise imports it from the same place.
from agent_runner.vcs_state import (  # noqa: E402,F401 — public primitives
    AutoCommitError,
    StashError,
    stash_orphan,
    try_auto_commit,
)


def narrate_events(log_dir: Path, *, poll_interval_s: float = 0.5) -> Iterator[str]:
    """Tail events-*.jsonl files in log_dir, yielding one formatted line per event.

    Format: ``[HH:MM:SS.fff] {event:<20} key=value ...`` (excluding ts and event).

    Polling-based (no inotify/kqueue — cross-platform). Designed for human-readable
    live monitoring during debug / audit / short runs. Yields events from byte 0
    of all files present at iterator start, then follows new appends.
    """
    for evt in _tail_events_jsonl(log_dir, start_at_now=False, poll_interval_s=poll_interval_s):
        yield _format_narrate_line(evt)


def stream_events_jsonl(log_dir: Path, *, poll_interval_s: float = 0.1) -> Iterator[dict[str, Any]]:
    """Tail events-*.jsonl files in log_dir, yielding one parsed event dict per line.

    Subscription begins at "now": events present in the file before the iterator
    starts are NOT yielded. Follows file rotation transparently (when a new
    events-YYYY-MM.jsonl appears, the iterator picks it up from byte 0).

    Default poll_interval_s of 0.1 reflects machine-consumption latency
    expectations (vs ``narrate_events`` which uses 0.5 for human pacing).

    Polling-based (no inotify/kqueue — cross-platform). Designed for machine
    consumption (vs ``narrate_events`` which formats for humans).
    """
    yield from _tail_events_jsonl(log_dir, start_at_now=True, poll_interval_s=poll_interval_s)


def relay_remote_events(
    host: str,
    *,
    log_dir: Path,
    kinds: Sequence[str] | None = None,
    remote_config: str | None = None,
    failure_tolerance_s: float = 90.0,
    out: TextIO | None = None,
) -> int:
    """Remote sibling of ``stream_events_jsonl``: relay ``host``'s event stream.

    Spawns a managed ``ssh <host> -- agent-runner events --tail`` and passes its
    JSONL through, reconnecting with ``--since`` so a dropped link replays its
    gap. Returns a CLI exit code (0 on interrupt, 1 once the link stays down
    past ``failure_tolerance_s``). Blocks until then.

    ``log_dir`` is this CLIENT's log dir: the ``monitor_remote_blip`` /
    ``monitor_remote_giveup`` events it writes describe the local machine's link
    to ``host``, not the supervised project. Detection is deliberately NOT
    relayed — the detectors run on ``host`` (see ``monitor_loop``).
    """
    # Late import: pulls agent_runtime (psutil, threading) only for the callers
    # that actually stream from a remote host.
    from agent_runner.remote_relay import relay_remote_events as _relay

    return _relay(
        host,
        log_dir=log_dir,
        kinds=kinds,
        remote_config=remote_config,
        failure_tolerance_s=failure_tolerance_s,
        out=out,
    )


def _format_narrate_line(evt: dict[str, Any]) -> str:
    """Format an event dict as a one-line human-readable string.

    Format: ``[HH:MM:SS.fff] {event:<20} {key=value pairs}``. ``ts`` and ``event``
    are extracted into the prefix; remaining top-level keys become ``key=value``.
    """
    ts = evt.get("ts", "")
    time_part = ts[11:23] if len(ts) > 23 else ts
    event = evt.get("event", "?")
    fields = {k: v for k, v in evt.items() if k not in ("ts", "event")}
    kv_parts = []
    for k, v in fields.items():
        if k == "round_num":
            # Cosmetic shorten: `round=N` is more scannable than `round_num=N` in
            # one-line narrate output. The wire field stays `round_num` in events.jsonl.
            kv_parts.append(f"round={v}")
        else:
            kv_parts.append(f"{k}={v}")
    kv = " ".join(kv_parts)
    return f"[{time_part}] {event:<20} {kv}"
