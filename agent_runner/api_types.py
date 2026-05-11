"""Dataclasses for the Python API state tree.

These are the public types that ``agent_runner.api`` returns and that
``cli/`` formats. Phase 3 LLM/Critic will read these structures.

All frozen — state is immutable, no in-place mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ServiceMode(StrEnum):
    SYSTEMD_USER = "systemd_user"
    PID_FILE = "pid_file"
    NONE = "none"


@dataclass(frozen=True)
class ServiceStatus:
    mode: ServiceMode
    active: bool
    pid: int | None = None
    uptime_s: float | None = None
    unit_file: Path | None = None


@dataclass(frozen=True)
class SystemMetrics:
    mem_total_mb: int
    mem_available_mb: int
    disk_used_pct: float
    disk_free_gb: float = 0.0
    load_1m: float | None = None
    cpu_pct: float | None = None


@dataclass(frozen=True)
class RoundView:
    round_num: int
    phase: str | None
    started_at: str
    duration_so_far_s: float | None
    pid: int | None
    exit_code: int | None
    timed_out: bool | None
    log_path: Path
    log_tail: str | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectState:
    project: str
    status: dict[str, Any]
    defenses: list[dict[str, Any]]
    current_round: RoundView | None
    recent_rounds: list[RoundView]
    orphan: dict[str, Any] | None
    system: SystemMetrics
    service: ServiceStatus
    recent_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Alert:
    severity: str
    detector: str
    message: str
    context: dict[str, Any]
    ts: str
    auto_action: str = "none"


@dataclass(frozen=True)
class InitResult:
    work_dir: Path
    files_created: list[Path]
    committed: bool


@dataclass(frozen=True)
class InstallResult:
    unit_path: Path
    monitor_unit_path: Path | None
    enabled: bool
    started: bool


def select_path(tree: Any, path: str) -> Any:
    """Resolve dot-notation path into a nested dataclass / dict / list tree.

    Numeric segments index into lists. Raises KeyError naming the failed
    segment so the caller (CLI) can give a precise error to the user.
    """
    cur = tree
    for part in path.split("."):
        try:
            if part.isdigit():
                cur = cur[int(part)]
            elif isinstance(cur, dict):
                cur = cur[part]
            else:
                cur = getattr(cur, part)
        except (AttributeError, IndexError, KeyError) as e:
            raise KeyError(f"path segment {part!r} not found in select path {path!r}") from e
    return cur
