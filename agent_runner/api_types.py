"""Dataclasses for the Python API state tree.

These are the public types that ``agent_runner.api`` returns and that
``cli/`` formats. Plugins (hooks, context enrichers, detectors) consume them.

All frozen — state is immutable, no in-place mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

Severity = Literal["info", "warning", "critical"]
"""Alert / Detector severity level. ``critical`` is the only level that may
trigger ``auto_action="stop_service"``."""

AutoAction = Literal["none", "stop_service"]
"""What the monitor does when this Alert fires. ``stop_service`` only takes
effect if the detector's ``name`` is in ``cfg.monitor.auto_stop_on``."""


class ServiceMode(StrEnum):
    SYSTEMD_USER = "systemd_user"
    PID_FILE = "pid_file"
    NONE = "none"


@dataclass(frozen=True)
class RateLimitState:
    """Public: surfaced via peek --json when supervisor is currently throttled."""

    throttled_until_epoch: int
    limit_type: str
    agent: str
    since_round: int


@dataclass(frozen=True)
class ServiceStatus:
    mode: ServiceMode
    active: bool
    pid: int | None = None
    uptime_s: float | None = None
    unit_file: Path | None = None
    rate_limit: RateLimitState | None = None


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
    recent_hook_failures: list[dict[str, Any]] = field(default_factory=list)
    recent_blips: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Alert:
    severity: Severity
    detector: str
    message: str
    context: dict[str, Any]
    ts: str
    auto_action: AutoAction = "none"


@runtime_checkable
class Detector(Protocol):
    """Public plugin contract for monitor detectors.

    Plugins implementing this Protocol can be registered via
    ``monitor.register_detector`` (or auto-loaded via entry_points group
    ``agent_runner.detectors``) and will be invoked alongside built-in
    detectors during each monitor poll.

    ``auto_action="stop_service"`` is honored only if the plugin's ``name``
    appears in ``cfg.monitor.auto_stop_on`` — operators must explicitly
    opt plugins into the auto-stop policy.
    """

    name: str
    severity: Severity
    auto_action: AutoAction

    def detect(self, state: ProjectState) -> Alert | None: ...


@dataclass(frozen=True)
class ThrottleState:
    """Internal: supervisor-detected active rate-limit window state.

    Reconstructed from events.jsonl tail (latest unmatched
    rate_limit_rejected). Surfaced via ServiceStatus.rate_limit.
    """

    reset_at_epoch: int
    limit_type: str
    agent: str
    since_round: int


@dataclass(frozen=True)
class RoundResult:
    """Result of one ``run_one_round`` call.

    Stable across 0.1.x for PostRoundHook consumers. Fields may be ADDED in
    a minor; removed or retyped only on a major bump.
    """

    round_num: int
    phase: str | None
    started_at: str
    ended_at: str
    exit_code: int
    duration_s: float
    timed_out: bool
    log_path: Path
    dirty_files: list[str]
    stashed: bool


@dataclass(frozen=True)
class InitResult:
    work_dir: Path
    files_created: list[Path]
    committed: bool
    preset: str = "claude"  # default keeps synthesised InitResults working


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
