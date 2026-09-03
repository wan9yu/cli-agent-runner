"""Service-lifecycle primitives: PID files, signal sending, service-mode detection.

Used by ``cli/serve_cmd.py`` (writes serve.pid) and ``cli/service_cmd.py``
(reads PID + signals it for stop/kill). Also tells callers whether the
project is managed by systemd-user or a plain serve process.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: TID251 — lifecycle probes systemd, sanctioned like api.py
from dataclasses import dataclass
from pathlib import Path

import psutil

from agent_runner import _resolve
from agent_runner.api_types import ServiceMode
from agent_runner.clock import Clock, wait_until
from agent_runner.context_store import atomic_write_json

# Bounds every `systemctl --user` call this module makes. A wedged D-Bus
# session (or a systemd that never answers) must not hang a lifecycle-safety
# verb (stop/kill/status) forever — it should fail fast and let the caller
# fall back (status) or surface the error (monitor's on_alert -> auto_stop_failed).
_SYSTEMCTL_TIMEOUT_S = 10

_SYSTEMD_ACTIVE_STATES = frozenset({"active", "activating", "reloading"})
_SYSTEMD_INACTIVE_STATES = frozenset({"failed", "inactive"})


def _systemctl_is_active(unit_name: str) -> str | None:
    """Patchable seam. Return the unit's `systemctl --user is-active` state
    string ("active"/"activating"/"failed"/...), or None when systemctl is
    absent, the call times out, or it errors — callers then fall back to
    serve.pid so status()/detect_service_mode() never crash or hang on a
    non-systemd host (darwin dev, containers). is-active prints the state
    even on a non-zero exit, so check=False keeps the string instead of
    raising CalledProcessError."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", unit_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None


def _systemctl_user(*args: str) -> None:
    """Run a NON-draining ``systemctl --user`` verb under the fixed timeout
    (enable/start/kill/daemon-reload). The draining ``stop`` verb goes through
    ``stop_unit_draining`` instead: a plain blocking ``systemctl --user stop``
    waits for the unit to go inactive, and serve DRAINS its in-flight round on
    SIGTERM (up to round_timeout_s), so a blocking stop under this timeout would
    raise ``TimeoutExpired`` for a stop systemd is completing normally."""
    subprocess.run(["systemctl", "--user", *args], check=True, timeout=_SYSTEMCTL_TIMEOUT_S)


def stop_unit_draining(unit_name: str, *, clock: Clock, confirm_s: float) -> bool:
    """Queue a graceful stop for a possibly-draining unit and poll ``is-active``
    until it reaches an inactive state or ``confirm_s`` elapses.

    A plain ``systemctl --user stop`` blocks until the unit is inactive, but
    serve DRAINS its in-flight round on SIGTERM (up to round_timeout_s), so a
    blocking stop under a subprocess timeout raises ``TimeoutExpired`` for a stop
    systemd is completing normally — the half-execution that leaves a healthy
    serve stopped. ``--no-block`` returns once the stop job is enqueued; this
    then polls. Returns True once confirmed inactive, False if still draining
    past the bound (best-effort "stop requested; not confirmed") — never raises
    ``TimeoutExpired`` for a normal drain. Mirrors ``api``'s PID_FILE
    ``_await_pid_exit`` best-effort confirm, one mode over."""
    _systemctl_user("--no-block", "stop", unit_name)
    return wait_until(
        clock,
        lambda: _systemctl_is_active(unit_name) in _SYSTEMD_INACTIVE_STATES,
        timeout_s=confirm_s,
    )


def _valid_pid(value: object) -> bool:
    """A PID we may signal: a real int (not bool — bool subclasses int) and > 1
    (pid 1 is init). Applied to BOTH read() branches so a legacy `true`/`1` file
    can't smuggle an unsignalable PID past the guard."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 1


@dataclass(frozen=True)
class PIDFile:
    path: Path

    def write(self, pid: int) -> None:
        """Record the pid plus its process start-time as an identity token, so a
        later read can tell "the serve I started" from a recycled PID. Written
        atomically (tmp + rename) so a concurrent ``stop``/``kill`` read never sees a
        torn file."""
        payload: dict[str, object] = {"pid": pid}
        try:
            payload["create_time"] = psutil.Process(pid).create_time()
        except psutil.Error:
            pass  # token is best-effort; a read without it falls back to unverified
        atomic_write_json(self.path, payload)

    def read(self) -> int | None:
        """Return the recorded pid ONLY if it still names the same process (matching
        start-time), else None — so ``stop``/``kill`` never signal a recycled PID
        after a crash-without-cleanup. A legacy bare-int file (no token) is returned
        unverified for back-compat and upgraded on the next write."""
        try:
            raw = self.path.read_text().strip()
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if isinstance(data, int):  # bool is an int subclass, so it is covered here too
            return data if _valid_pid(data) else None  # legacy bare-int, still guarded
        if not isinstance(data, dict) or not _valid_pid(data.get("pid")):
            return None
        pid = data["pid"]
        recorded = data.get("create_time")
        if recorded is None:
            return pid  # write couldn't capture a token; best-effort unverified
        try:
            if abs(psutil.Process(pid).create_time() - recorded) < 1.0:
                return pid
        except psutil.Error:
            pass
        return None  # process gone, or a different (recycled) process now holds the PID

    def unlink(self) -> None:
        self.path.unlink(missing_ok=True)


def pid_alive(pid: int) -> bool:
    """True iff the process exists and we have permission to signal it."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def send_signal_to_pid(pid: int, sig: int) -> bool:
    """Send ``sig`` to ``pid``. Returns True on success, False if pid gone / forbidden."""
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _user_systemd_dir() -> Path:
    """Patchable in tests."""
    return Path.home() / ".config" / "systemd" / "user"


def detect_service_mode(project: str, *, log_dir: Path) -> ServiceMode:
    """Decide how this project is managed: systemd unit, plain pidfile, or nothing.

    Precedence is is-active-GATED, not pidfile-vs-unit — serve writes serve.pid
    in BOTH modes (serve_cmd.py), so a plain "pidfile exists" check can't tell
    a healthy systemd serve from a hand-launched one. Ranking a live pidfile
    above an installed-but-healthy unit would misroute `restart` (which
    requires SYSTEMD_USER) into "PID_FILE, refuse":
    - unit exists AND is-active -> SYSTEMD_USER (systemd owns it; restart works)
    - unit exists AND NOT active AND a live serve.pid -> PID_FILE (systemd's
      record is stale/failed; something is actually running the plain way —
      signal what's really alive, not the dead unit)
    - unit exists AND NOT active AND no live pid -> SYSTEMD_USER (nothing to
      signal directly; `start` needs the systemd path to respawn it)
    - no unit: PID_FILE if a pidfile exists, else NONE
    """
    unit_name = _resolve.unit_filename(project)
    unit = _user_systemd_dir() / unit_name
    if not unit.exists():
        if (log_dir / "serve.pid").exists():
            return ServiceMode.PID_FILE
        return ServiceMode.NONE
    if _systemctl_is_active(unit_name) in _SYSTEMD_ACTIVE_STATES:
        return ServiceMode.SYSTEMD_USER
    pid = PIDFile(log_dir / "serve.pid").read()
    if pid is not None and pid_alive(pid):
        return ServiceMode.PID_FILE
    return ServiceMode.SYSTEMD_USER
