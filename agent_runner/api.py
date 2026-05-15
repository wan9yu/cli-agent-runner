"""Public Python API mirroring CLI verbs.

Every CLI subcommand has a corresponding api function. CLI files do
``api.X(...)`` and format the returned dataclass for display. External
callers can ``from agent_runner import api`` and skip CLI text parsing
entirely.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import signal
import subprocess  # noqa: TID251 — api uses systemctl + ssh, both subprocess
import sysconfig
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_runner import events, lifecycle
from agent_runner.api_types import (
    InitResult,
    InstallResult,
    ProjectState,
    ServiceMode,
    ServiceStatus,
    select_path,
)
from agent_runner.config import Config, RuntimeConfig, load_config
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

_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_LINGER_HINT = (
    "On headless distros, run `sudo loginctl enable-linger $USER` and "
    "re-login, OR pass `--system` for a system-level unit."
)


def _project_name(work_dir: Path) -> str:
    name = work_dir.resolve().name or "default"
    if not _PROJECT_NAME_RE.match(name):
        raise ValueError(
            f"invalid project name {name!r}: must match [A-Za-z0-9._-]+. "
            "The project name is the basename of work_dir and is interpolated into "
            "ssh remote commands and systemd unit filenames; shell metacharacters "
            "and path separators are rejected."
        )
    return name


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
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "systemctl binary not found in PATH; user systemd is not available. " + _LINGER_HINT
        ) from exc
    if "Failed to connect to bus" in (probe.stderr or ""):
        raise RuntimeError("user systemd unavailable (D-Bus session not running). " + _LINGER_HINT)


def _systemctl_user(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True)


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


def _install_system(cfg: Config, project: str, *, with_monitor: bool) -> InstallResult:
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
    serve_path.write_text(render_serve_unit(cfg, script_path=script_path, user=sudo_user))
    monitor_path: Path | None = None
    if with_monitor:
        monitor_path = _SYSTEM_UNITS_DIR / monitor_unit_filename(project)
        monitor_path.write_text(render_monitor_unit(cfg, script_path=script_path, user=sudo_user))
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
    work_dir: Path | None = None, *, system: bool = False, with_monitor: bool = False
) -> InstallResult:
    if work_dir is None:
        work_dir = Path.cwd()
    cfg_path = work_dir / "agent-runner.toml"
    cfg = load_config(cfg_path)
    project = _project_name(work_dir)

    if system:
        return _install_system(cfg, project, with_monitor=with_monitor)

    _check_user_systemd_available()
    script_path = _agent_runner_script_path()

    units_dir = lifecycle._user_systemd_dir()
    units_dir.mkdir(parents=True, exist_ok=True)

    serve_path = units_dir / serve_unit_filename(project)
    serve_path.write_text(render_serve_unit(cfg, script_path=script_path))

    monitor_path: Path | None = None
    if with_monitor:
        monitor_path = units_dir / monitor_unit_filename(project)
        monitor_path.write_text(render_monitor_unit(cfg, script_path=script_path))

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
from agent_runner.events import (  # noqa: E402
    AGENT_NETWORK_BLIP,
    HOOK_FAILED,
    MONITOR_REMOTE_BLIP,
    MONITOR_REMOTE_GIVEUP,
)

_RECENT_HOOK_FAILURES_LIMIT = 10
_RECENT_BLIPS_LIMIT = 5


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
        recent_hook_failures=recent_hook_failures,
        recent_blips=recent_blips,
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
        phases_overrides=cfg.phases.overrides if cfg.phases.overrides else None,
    )
    if not monitor._PLUGIN_DETECTORS:
        return builtin  # skip ProjectState assembly when no plugins to feed
    state = monitor.assemble_project_state(src, project=_project_name(work_dir))
    plugin = monitor.run_plugin_detectors(state)
    return builtin + plugin


# Backoff schedule for remote-failure retries: each element is the sleep duration
# in seconds for that attempt index (capped at 30s per step).
_REMOTE_FAILURE_BACKOFF = (1, 2, 4, 8, 16, 30)


def monitor_loop(
    project: str | Path | None = None, *, host: str | None = None, interval_s: int = 30
) -> Iterator[monitor.Alert]:
    """Yield alerts as they're detected. Caller decides what to do.

    The loop dedups alerts by (detector, json.dumps(context)) within session.
    Emits ``monitor_started`` once at entry — programmatic consumers can subscribe
    to that kind as the canonical "supervision is up" signal (monitor is otherwise
    silent during healthy operation by design).

    Tolerates transient ``MonitorRemoteError`` failures (from ``--host`` ssh)
    for up to ``cfg.monitor.remote_failure_tolerance_s`` seconds with exponential
    backoff (1s → 2s → 4s → ... → 30s cap). Each retry emits ``monitor_remote_blip``;
    crossing the cap emits one ``monitor_remote_giveup`` and propagates the error
    (CLI exits 1; systemd restarts the process). Setting tolerance to 0 preserves
    the 0.1.10 immediate-propagate behavior with no blip events emitted.
    """
    import json as _json

    seen: set[str] = set()
    work_dir = project if isinstance(project, Path) else Path.cwd()
    cfg = load_config(work_dir / "agent-runner.toml")
    cfg.runtime.log_dir.mkdir(parents=True, exist_ok=True)
    events.emit(
        cfg.runtime.log_dir,
        "monitor_started",
        host=host,
        interval_s=interval_s,
        log_dir=str(cfg.runtime.log_dir),
        mode="anomaly-only",
    )

    tolerance_s = cfg.monitor.remote_failure_tolerance_s
    blip_start: float | None = None
    attempt = 0

    while True:
        try:
            alerts = _poll_once(work_dir, host=host)
            # Success: reset retry state.
            blip_start = None
            attempt = 0
        except monitor.MonitorRemoteError as e:
            if tolerance_s == 0:
                raise  # 0.1.10 behavior preserved
            now = time.monotonic()
            if blip_start is None:
                blip_start = now
            attempt += 1
            elapsed = now - blip_start
            if elapsed >= tolerance_s:
                events.emit(
                    cfg.runtime.log_dir,
                    MONITOR_REMOTE_GIVEUP,
                    host=host,
                    total_attempts=attempt,
                    total_elapsed_s=elapsed,
                    cap_s=tolerance_s,
                    final_error=e.stderr,
                )
                raise
            backoff_idx = min(attempt - 1, len(_REMOTE_FAILURE_BACKOFF) - 1)
            next_sleep_s = min(_REMOTE_FAILURE_BACKOFF[backoff_idx], tolerance_s - elapsed)
            events.emit(
                cfg.runtime.log_dir,
                MONITOR_REMOTE_BLIP,
                host=host,
                error=e.stderr,
                attempt=attempt,
                elapsed_s=elapsed,
                cap_s=tolerance_s,
                interval_s=interval_s,
                next_sleep_s=next_sleep_s,
            )
            time.sleep(next_sleep_s)
            continue

        for alert in alerts:
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
                allowed_stop_names=cfg.monitor.auto_stop_on,
            )
        time.sleep(interval_s)


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

    Follows file rotation transparently — when a new events-YYYY-MM-DD.jsonl
    appears, it is picked up from byte 0.
    """
    import json as _json
    import time as _time

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
            with path.open("r", encoding="utf-8") as f:
                f.seek(pos)
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        evt = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    yield evt
                    any_new = True
                seen_positions[path] = f.tell()
        if not any_new:
            _time.sleep(poll_interval_s)


def _primary_prompt_file(cfg: Config) -> Path | None:
    """Return the primary prompt file: first of cfg.prompt.files, else cfg.prompt.file.

    Used by HookContext to give hooks a single Path to inspect (e.g.
    prompt-mutation hash tracking). Internal — runner is the only caller.
    """
    if cfg.prompt.files:
        return cfg.prompt.files[0]
    return cfg.prompt.file


def assemble_prompt(
    cfg: Config, phase: str | None, *, context: dict[str, Any] | None = None
) -> str:
    """Assemble the prompt for a given round.

    Resolves per-phase prompt.files override (via PhaseOverride.prompt_files); falls
    back to cfg.prompt.files OR cfg.prompt.file (back-compat single-file). Applies
    concat_separator, optionally strips first-file YAML frontmatter, injects context
    block per cfg.prompt.inject_context + context_injection_mode.

    Returns the assembled prompt text passed to the agent subprocess.
    """
    from agent_runner import prompt_loader

    # Determine files list (per-phase override → global files → single-file fallback)
    files: list[Path]
    override = cfg.phases.overrides.get(phase) if phase is not None else None
    if override is not None and override.prompt_files is not None:
        files = override.prompt_files
    elif cfg.prompt.files:
        files = cfg.prompt.files
    elif cfg.prompt.file is not None:
        files = [cfg.prompt.file]
    else:
        raise ValueError("no prompt files configured (set prompt.files or prompt.file)")

    # Resolve relative paths against work_dir
    resolved = [f if f.is_absolute() else (cfg.runtime.work_dir / f) for f in files]

    return prompt_loader.assemble_prompt(
        resolved,
        context=context,
        inject_context=cfg.prompt.inject_context,
        mode=cfg.prompt.context_injection_mode,
        concat_separator=cfg.prompt.concat_separator,
        strip_first_frontmatter=cfg.prompt.strip_yaml_frontmatter,
    )


def resolve_runtime_for_phase(cfg: Config, phase_name: str | None) -> RuntimeConfig:
    """Return effective RuntimeConfig for the given phase.

    Merges base ``cfg.runtime`` with ``cfg.phases.overrides[phase_name]`` (if
    present). ``None`` phase_name returns base unchanged. Unknown phase_name
    silently returns base — config-load is responsible for typo catching;
    this function is defensive.
    """
    base = cfg.runtime
    if phase_name is None:
        return base
    override = cfg.phases.overrides.get(phase_name)
    if override is None:
        return base
    updates = {}
    if override.round_timeout_s is not None:
        updates["round_timeout_s"] = override.round_timeout_s
    if override.disable_pre_round_hooks is not None:
        updates["disable_pre_round_hooks"] = override.disable_pre_round_hooks
    if not updates:
        return base
    return dataclasses.replace(base, **updates)


def read_round_num(log_dir: Path) -> int:
    """Return the most recent round_num from status.json (or 0 if missing/corrupt).

    Used by ``agent-runner serve`` to coordinate per-round log filenames with
    the round_num that the round subprocess itself writes to events.jsonl —
    ensures ``round-<N>.log`` file matches the ``round_num`` field in events.
    """
    from agent_runner.context_store import read_status

    s = read_status(log_dir)
    return s.round_num if s is not None else 0


def read_sentinel_content(log_dir: Path) -> str | None:
    """Return sentinel content (capped 200) or None if absent.

    Used by both ``check_self_terminated_sentinel`` (which emits an event) and
    HTTP progress rendering (which displays the reason). Identical read logic
    with ``errors='replace'`` for non-UTF-8 robustness.
    """
    sentinel = log_dir / ".agent-done"
    if not sentinel.exists():
        return None
    try:
        return sentinel.read_text(encoding="utf-8", errors="replace")[:200]
    except OSError:
        return ""


def check_self_terminated_sentinel(log_dir: Path) -> bool:
    """Check for ``log_dir/.agent-done`` and emit ``agent_self_terminated`` if present.

    Returns True if sentinel was found and event emitted (caller should stop),
    False otherwise. Reason text is read with errors='replace' for non-UTF-8
    robustness and truncated to 200 chars in the event payload.
    """
    from agent_runner import events

    reason = read_sentinel_content(log_dir)
    if reason is None:
        return False
    events.emit(log_dir, events.SELF_TERMINATED, reason=reason)
    return True


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
    events-YYYY-MM-DD.jsonl appears, the iterator picks it up from byte 0).

    Default poll_interval_s of 0.1 reflects machine-consumption latency
    expectations (vs ``narrate_events`` which uses 0.5 for human pacing).

    Polling-based (no inotify/kqueue — cross-platform). Designed for machine
    consumption (vs ``narrate_events`` which formats for humans).
    """
    yield from _tail_events_jsonl(log_dir, start_at_now=True, poll_interval_s=poll_interval_s)


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
