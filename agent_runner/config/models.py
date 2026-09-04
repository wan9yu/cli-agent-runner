"""Config dataclasses, their defaults, and the value-set/field-set frozensets
derived from them.

No validation logic lives here — that's validators.py's job. This module is
pure data shape: what a ``Config`` looks like once parsed, and the SSOT
frozensets (``_VALID_*`` for Literal value sets, ``_*_ALLOWED_FIELDS`` for
per-table unknown-key rejection) that both the loader and ``migrations.py``
consult so the two never drift apart.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent_runner import schedule

_VALID_INJECTION_MODES: frozenset[str] = frozenset({"prepend", "file", "none"})
_VALID_DIRTY_ACTIONS: frozenset[str] = frozenset({"stash", "ignore", "auto_commit"})
_VALID_TRANSIENT_ERROR_ACTIONS: frozenset[str] = frozenset({"back_off", "skip", "stop"})
_VALID_PROMPT_DELIVERY: frozenset[str] = frozenset({"argv", "stdin"})


@dataclass(frozen=True)
class AgentConfig:
    command: list[str]
    prompt_arg_template: list[str]
    name: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    prompt_delivery: Literal["argv", "stdin"] = "argv"

    @property
    def binary(self) -> str | None:
        """The agent's identity for throttle-skip and hook/metrics joins: the basename
        of ``command[0]``, which is exactly the label a detector stamps on
        ``transient_error_detected``. ``None`` only if ``command`` is empty. This is the
        real join key (unlike the cosmetic, optional ``name``), defined once here so
        serve and runner don't each re-spell ``Path(command[0]).name``."""
        return Path(self.command[0]).name if self.command else None


@dataclass(frozen=True)
class RuntimeConfig:
    work_dir: Path
    log_dir: Path
    round_timeout_s: int = 1800
    restart_delay_s: int = 3
    disable_pre_round_hooks: bool = False
    round_log_retention: int = 0  # 0 = never prune (pruning is opt-in)
    narrative_file: Path | None = None
    transient_error_action: Literal["back_off", "skip", "stop"] = "back_off"
    max_rounds: int | None = None  # None = unbounded
    stop_file: Path | None = None  # None = disabled
    substrate_fingerprint_paths: list[str] = field(default_factory=list)
    fresh_eyes_every_n: int | None = None  # None = disabled
    dry_run: bool = False
    max_grace_after_result_s: int = 0  # 0 = disabled
    grace_kill_ignore_patterns: list[str] = field(default_factory=list)
    """Regex patterns (re.search) tested against each child process's joined
    cmdline. Matching children are excluded from the grace-kill liveness
    check — for persistent helper subprocesses (e.g. claude's shell-snapshot
    bash) that would otherwise defeat max_grace_after_result_s. Empty list
    = no filtering (0.1.38 behavior preserved)."""


@dataclass(frozen=True)
class PhaseOverride:
    """Per-phase override for selected RuntimeConfig + PromptConfig fields.

    Each field is Optional; None means "no override, use base value". The
    whitelist of fields here matches the allowed [phases.<name>] sub-table
    fields documented in docs/configuration.md.
    """

    round_timeout_s: int | None = None
    disable_pre_round_hooks: bool | None = None
    prompt_files: list[Path] | None = None
    agent: AgentConfig | None = None
    schedule: ScheduleConfig | None = None


@dataclass(frozen=True)
class PhasesConfig:
    """Phases section: optional rotation list + per-phase override sub-tables.

    Replaces the old raw ``list[str] | None`` shape on ``Config.phases``. Code
    reading ``cfg.phases`` directly as a list must migrate to ``cfg.phases.list``.
    """

    list: list[str] | None = None
    overrides: dict[str, PhaseOverride] = field(default_factory=dict)
    phase_policy: Literal["wait", "skip"] = "wait"


@dataclass(frozen=True)
class PromptConfig:
    file: Path | None = None  # back-compat single-file; mutually exclusive with files
    files: list[Path] = field(default_factory=list)
    inject_context: bool = True
    context_injection_mode: Literal["prepend", "file", "none"] = "prepend"
    concat_separator: str = "\n\n"
    strip_yaml_frontmatter: bool = True


@dataclass(frozen=True)
class VcsConfig:
    stash_idempotency_s: int = 5
    dirty_action: Literal["stash", "ignore", "auto_commit"] = "stash"


# Default auth-failure detection regex — matches common OAuth/401/expired-session
# vocabularies. Presets override [monitor].auth_fail_hint per CLI.
_DEFAULT_AUTH_PATTERNS: list[str] = [
    r"\b(oauth|unauthorized|401|api[_ ]key|"
    r"auth(entication)?[_ -]?(failed|error|expired)|session.*expired)\b",
]
# Default auth-failure hint is empty — per-CLI hints come from preset files
# (agent_runner/presets/*.toml) which write `[monitor].auth_fail_hint` into the
# user's agent-runner.toml at scaffold time.
_DEFAULT_AUTH_HINT: str = ""

# Default allow-list of detector names whose ``stop_service`` action is honored.
# Plugin detectors must be added explicitly by the operator to opt them in.
_DEFAULT_AUTO_STOP_ON: tuple[str, ...] = ("oauth_fail", "disk_critical")

# How long the event relay (``monitor --host X --mode events``) keeps
# reconnecting a dropped ssh link before it gives up and exits 1.
# 0 = opt-out (the first ssh exit is fatal, no reconnect).
_DEFAULT_REMOTE_FAILURE_TOLERANCE_S: int = 90


@dataclass(frozen=True)
class PluginsConfig:
    """Plugin-related TOML knobs.

    Migrating from free-form ``dict[str, Any] | None`` (0.1.11 and earlier) to a
    typed dataclass. Known keys are first-class fields; unknown keys land in
    ``.raw`` for forward-compatibility with plugin-author-defined `[plugins.*]`
    sub-keys (e.g. plugin packages may read their own config from `cfg.plugins.raw`).

    Neither field is read by core, so both will read as dead to a reader grepping
    for uses. They are published contracts (CHANGELOG 0.1.12) and are deliberately
    kept — guarded by tests/invariants/test_plugins_config_stable.py.
    """

    disable: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitorHostHealthConfig:
    """Thresholds for the host-health detectors (mem_pressure, disk_warning, disk_critical).

    Defaults match the current hardcoded detector values — existing deployments are
    unaffected unless the operator explicitly sets a [monitor.host_health] section.
    """

    mem_avail_min_mb: int = 200  # mem_pressure fires when mem_available_mb < this
    disk_warning_pct: float = 90.0  # disk_warning fires when disk_used_pct >= this
    disk_critical_pct: float = 95.0  # disk_critical fires when disk_used_pct >= this
    swap_sout_noise_floor_mb: int = 32  # tier-2 swap-out noise floor (MiB); compare uses *1024*1024
    mem_free_low_mb: int = 16  # tier-3 MemFree floor (MB)


@dataclass(frozen=True)
class MonitorConfig:
    auth_fail_patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_AUTH_PATTERNS))
    auth_fail_hint: str = _DEFAULT_AUTH_HINT
    auto_stop_on: list[str] = field(default_factory=lambda: list(_DEFAULT_AUTO_STOP_ON))
    remote_failure_tolerance_s: int = _DEFAULT_REMOTE_FAILURE_TOLERANCE_S
    anomaly_repetitive_window: int = 0  # 0 = disabled
    anomaly_repetitive_threshold: int = 0  # 0 = disabled
    host_health: MonitorHostHealthConfig = field(default_factory=MonitorHostHealthConfig)
    round_progress_interval_s: int = 0  # 0 = disabled; >0 = emit round_progress every N seconds
    supervisor_stale_threshold_s: int | None = None
    """Staleness deadline for the supervisor_stale detector (seconds).

    None (unset) → derived default round_timeout_s * 1.5.
    Positive int → explicit threshold. 0 → disable the detector.
    """


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: str | None = None  # IANA name; None = host local time
    run_windows: tuple[schedule.Window, ...] = ()
    pause_windows: tuple[schedule.Window, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.run_windows or self.pause_windows)


@dataclass(frozen=True)
class Profile:
    """Fully-resolved per-phase execution profile.

    Produced by :meth:`Config.profile_for`: the base config with a phase's
    overrides applied. ``prompt_files`` is ``None`` when the phase sets no
    ``[phases.<name>].prompt`` override (the caller derives files from
    ``cfg.prompt``); an explicit ``prompt.files = []`` stays ``[]`` — distinct
    from ``None``.
    """

    agent: AgentConfig
    runtime: RuntimeConfig
    schedule: ScheduleConfig
    prompt_files: list[Path] | None


@dataclass(frozen=True)
class Config:
    agent: AgentConfig
    runtime: RuntimeConfig
    prompt: PromptConfig
    vcs: VcsConfig = field(default_factory=VcsConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    phases: PhasesConfig = field(default_factory=PhasesConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

    def profile_for(self, phase: str | None) -> Profile:
        """Resolve the effective execution profile for a phase.

        ``None`` phase (or an unknown phase with no override) returns the base
        agent/runtime/schedule by identity — matching the no-override early
        return of the old ``resolve_runtime_for_phase``, so existing configs are
        behavior-neutral. A per-phase ``[phases.<name>.schedule]`` with no
        ``timezone`` inherits the global timezone at resolve time.
        """
        ov = self.phases.overrides.get(phase) if phase is not None else None
        if ov is None:
            return Profile(self.agent, self.runtime, self.schedule, None)
        runtime = self.runtime
        rt_updates: dict[str, Any] = {}
        if ov.round_timeout_s is not None:
            rt_updates["round_timeout_s"] = ov.round_timeout_s
        if ov.disable_pre_round_hooks is not None:
            rt_updates["disable_pre_round_hooks"] = ov.disable_pre_round_hooks
        if rt_updates:
            runtime = dataclasses.replace(runtime, **rt_updates)
        sched = self.schedule
        if ov.schedule is not None:
            sched = ov.schedule
            if ov.schedule.timezone is None and self.schedule.timezone is not None:
                sched = dataclasses.replace(ov.schedule, timezone=self.schedule.timezone)
        return Profile(ov.agent or self.agent, runtime, sched, ov.prompt_files)


_PHASE_OVERRIDE_ALLOWED_FIELDS = frozenset(
    {
        "round_timeout_s",
        "disable_pre_round_hooks",
        "prompt",
        "agent",
        "runtime",
        "schedule",
    }
)

# Keys allowed under [phases.<name>.runtime] — the flat-alias twins.
_PHASE_RUNTIME_ALLOWED_FIELDS = frozenset({"round_timeout_s", "disable_pre_round_hooks"})

# Field names of AgentConfig — the keys a [phases.<name>.agent] sub-table may
# set (merged onto the base [agent] table before validation), and also the
# base [agent] table's own allowed keys (0.2.13: unknown [agent] keys reject).
_AGENT_ALLOWED_FIELDS = frozenset(f.name for f in dataclasses.fields(AgentConfig))

# Field names of RuntimeConfig/VcsConfig/MonitorConfig — the keys their base
# TOML tables accept. Renamed/removed keys (runtime.round_timeout_per_phase,
# runtime.rate_limit_action, vcs.orphan_action) are checked separately with a
# dedicated migration message BEFORE these sets are consulted, so they never
# need to appear here.
_RUNTIME_ALLOWED_FIELDS = frozenset(f.name for f in dataclasses.fields(RuntimeConfig))
_VCS_ALLOWED_FIELDS = frozenset(f.name for f in dataclasses.fields(VcsConfig))
_MONITOR_ALLOWED_FIELDS = frozenset(f.name for f in dataclasses.fields(MonitorConfig))

# Keys allowed under [monitor.host_health] — the 0.2.14 strictness completion
# (the exact footgun class an operator's typo'd threshold silently dropped).
_MONITOR_HOST_HEALTH_ALLOWED_FIELDS = frozenset(
    f.name for f in dataclasses.fields(MonitorHostHealthConfig)
)

# Keys allowed under a [phases.<name>.prompt] sub-table — `files` only
# (docs/configuration.md's per-phase table already promised this; 0.2.13
# makes the loader enforce it instead of silently ignoring the rest).
_PHASE_PROMPT_ALLOWED_FIELDS = frozenset({"files"})

# Keys allowed under the top-level [prompt] table.
_PROMPT_ALLOWED_FIELDS = frozenset(
    {
        "file",
        "files",
        "inject_context",
        "context_injection_mode",
        "concat_separator",
        "strip_yaml_frontmatter",
    }
)

# Keys allowed under a [schedule] table (top-level or per-phase).
_SCHEDULE_ALLOWED_FIELDS = frozenset({"timezone", "run_windows", "pause_windows"})
