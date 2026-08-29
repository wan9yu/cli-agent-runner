"""Service-lifecycle primitives: PID files, signal sending, service-mode detection.

Used by ``cli/serve_cmd.py`` (writes serve.pid) and ``cli/service_cmd.py``
(reads PID + signals it for stop/kill). Also tells callers whether the
project is managed by systemd-user or a plain serve process.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import psutil

from agent_runner.api_types import ServiceMode


@dataclass(frozen=True)
class PIDFile:
    path: Path

    def write(self, pid: int) -> None:
        """Record the pid plus its process start-time as an identity token, so a
        later read can tell "the serve I started" from a recycled PID."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {"pid": pid}
        try:
            payload["create_time"] = psutil.Process(pid).create_time()
        except psutil.Error:
            pass  # token is best-effort; a read without it falls back to unverified
        self.path.write_text(json.dumps(payload))

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
        if isinstance(data, int):
            return data  # legacy bare-int format: unverified
        if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
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
    """Decide how this project is managed: systemd unit, plain pidfile, or nothing."""
    unit = _user_systemd_dir() / f"agent-runner@{project}.service"
    if unit.exists():
        return ServiceMode.SYSTEMD_USER
    if (log_dir / "serve.pid").exists():
        return ServiceMode.PID_FILE
    return ServiceMode.NONE
