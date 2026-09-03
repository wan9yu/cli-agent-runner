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

from agent_runner.config.errors import ConfigError
from agent_runner.config.loader import load_config
from agent_runner.config.models import (  # noqa: F401 — public re-export
    _AGENT_ALLOWED_FIELDS,
    _DEFAULT_AUTH_HINT,
    _DEFAULT_AUTH_PATTERNS,
    _DEFAULT_AUTO_STOP_ON,
    _DEFAULT_REMOTE_FAILURE_TOLERANCE_S,
    _MONITOR_ALLOWED_FIELDS,
    _MONITOR_HOST_HEALTH_ALLOWED_FIELDS,
    _PHASE_PROMPT_ALLOWED_FIELDS,
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
)
from agent_runner.config.validators import _reject_control_chars  # noqa: F401

__all__ = [
    "AgentConfig",
    "Config",
    "ConfigError",
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
    "load_config",
]
