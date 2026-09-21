"""TOML config loader with dataclass-based validation.

Public facade over the ``config`` package: every sibling module in
``agent_runner`` (and every test) imports from ``agent_runner.config``
directly, never from ``agent_runner.config.<submodule>`` — this file is the
one place a name crosses that boundary. The re-export list below is
enumerated from actual cross-module imports (``grep -rn "from
agent_runner.config import\\|from agent_runner import config" agent_runner/
tests/``), not guessed: a name missing here is an ``ImportError`` at import
time for whichever module reaches for it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runner.config.errors import ConfigError
from agent_runner.config.loader import (  # noqa: F401 — public re-export
    _CURRENT_SCHEMA_VERSION,
    load_config,
)
from agent_runner.config.models import (  # noqa: F401 — public re-export
    _AGENT_ALLOWED_FIELDS,
    _DEFAULT_AUTH_HINT,
    _DEFAULT_AUTH_PATTERNS,
    _DEFAULT_AUTO_STOP_ON,
    _DEFAULT_REMOTE_FAILURE_TOLERANCE_S,
    _HOST_HEALTH_DISK_ALLOWED_FIELDS,
    _HOST_HEALTH_MEMORY_ALLOWED_FIELDS,
    _HOST_HEALTH_PRESSURE_ALLOWED_FIELDS,
    _MONITOR_ALLOWED_FIELDS,
    _MONITOR_HOST_HEALTH_ALLOWED_FIELDS,
    _PHASE_PROMPT_ALLOWED_FIELDS,
    _PLUGINS_ALLOWED_FIELDS,
    _PROMPT_ALLOWED_FIELDS,
    _RUNTIME_ALLOWED_FIELDS,
    _SCHEDULE_ALLOWED_FIELDS,
    _VALID_DIRTY_ACTIONS,
    _VALID_INJECTION_MODES,
    _VALID_PROMPT_DELIVERY,
    _VALID_TRANSIENT_ERROR_ACTIONS,
    _VCS_ALLOWED_FIELDS,
    AgentConfig,
    Config,
    GoalConfig,
    MonitorConfig,
    MonitorHostHealthConfig,
    PhaseOverride,
    PhasesConfig,
    PluginsConfig,
    Profile,
    PromptConfig,
    RuntimeConfig,
    ScheduleConfig,
    VcsConfig,
    _GoalCheckConfig,
    _HostHealthBrakeConfig,
    _HostHealthDiskConfig,
    _HostHealthMemoryConfig,
    _HostHealthPressureConfig,
)
from agent_runner.config.validators import (  # noqa: F401 — public re-export
    _reject_control_chars,
    _resolve_against_work_dir,
)


def _digest_mod():
    """Keep hashlib off the serve startup graph."""
    import importlib

    return importlib.import_module("agent_runner.config.digest")


def config_digest(cfg: Config, phase: str | None) -> str:
    return _digest_mod().config_digest(cfg, phase)


def snapshot_fields(cfg: Config, phase: str | None) -> dict[str, Any]:
    return _digest_mod().snapshot_fields(cfg, phase)


def round_start_fields(
    cfg: Config, phase: str | None, log_dir: Path, round_num: int
) -> dict[str, Any]:
    return _digest_mod().round_start_fields(cfg, phase, log_dir, round_num)


__all__ = [
    "AgentConfig",
    "Config",
    "ConfigError",
    "GoalConfig",
    "MonitorConfig",
    "MonitorHostHealthConfig",
    "PhaseOverride",
    "PhasesConfig",
    "PluginsConfig",
    "Profile",
    "PromptConfig",
    "RuntimeConfig",
    "ScheduleConfig",
    "VcsConfig",
    "config_digest",
    "load_config",
    "round_start_fields",
    "snapshot_fields",
]
