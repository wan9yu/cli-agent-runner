"""Service-lifecycle primitives: PID files, signal sending, service-mode detection.

Used by ``cli/serve_cmd.py`` (writes serve.pid) and ``cli/service_cmd.py``
(reads PID + signals it for stop/kill). Also tells callers whether the
project is managed by systemd-user or a plain serve process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent_runner.api_types import ServiceMode


@dataclass(frozen=True)
class PIDFile:
    path: Path

    def write(self, pid: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(pid))

    def read(self) -> int | None:
        try:
            return int(self.path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

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
