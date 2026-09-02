"""TOML config loader with dataclass-based validation."""

from __future__ import annotations

import dataclasses
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent_runner import schedule

_VALID_INJECTION_MODES: frozenset[str] = frozenset({"prepend", "file", "none"})
_VALID_DIRTY_ACTIONS: frozenset[str] = frozenset({"stash", "ignore", "auto_commit"})
_VALID_TRANSIENT_ERROR_ACTIONS: frozenset[str] = frozenset({"back_off", "skip", "stop"})
_VALID_PROMPT_DELIVERY: frozenset[str] = frozenset({"argv", "stdin"})


class ConfigError(ValueError):
    """Raised when a config TOML contains a removed or invalid field.

    Subclasses ValueError: pre-0.2.2 callers catching ValueError from
    load_config keep working. tests/invariants/test_config_error_consistency.py
    pins both the subclass relationship and the absence of bare ValueError here.
    """


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


def _require(d: dict, *path: str) -> object:
    cur: object = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            raise ConfigError(f"missing required field: {'.'.join(path)}")
        cur = cur[p]
    return cur


def _expand_path(s: str, project_name: str) -> Path:
    return Path(s.replace("{project}", project_name)).expanduser()


def _resolve_against_work_dir(p: Path | None, work_dir: Path) -> Path | None:
    """Return absolute path: None passes through, abs unchanged, relative joined to work_dir."""
    if p is None:
        return None
    return p if p.is_absolute() else (work_dir / p).resolve()


def _expand_and_resolve(s: str, project_name: str, work_dir: Path) -> Path:
    """Expand ~ and {project} in s, then resolve relative paths against work_dir."""
    return _resolve_against_work_dir(_expand_path(s, project_name), work_dir)  # type: ignore[return-value]


def _require_positive_int(value: Any, *, field: str) -> int:
    """Validate a TOML value is a positive int. Rejects bool (subclass of int
    in Python, would silently coerce e.g. ``true`` → 1) and any non-int."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field}: must be an integer, got {type(value).__name__} ({value!r})")
    if value <= 0:
        raise ConfigError(f"{field}: must be positive, got {value}")
    return value


def _require_bool(value: Any, *, field: str) -> bool:
    """Validate a TOML value is a bool. Distinct from int (in TOML, bool ≠ int)."""
    if not isinstance(value, bool):
        raise ConfigError(f"{field}: must be a bool, got {type(value).__name__} ({value!r})")
    return value


def _require_non_negative_int(value: Any, *, field: str) -> int:
    """Validate a TOML value is a non-negative int (allows 0). Rejects bool
    and any non-int. Sibling of _require_positive_int where 0 has meaning
    (e.g. opt-out / disable)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field}: must be an integer, got {type(value).__name__} ({value!r})")
    if value < 0:
        raise ConfigError(f"{field}: must be >= 0, got {value}")
    return value


def _require_str_list(value: Any, *, field: str) -> list[str]:
    """Validate a TOML value is a list (not a bare string or scalar) and return
    its elements as strings. The bare-string case is the footgun this rejects:
    ``command = "claude"`` would otherwise ``list()``-explode into
    ``['c','l','a','u','d','e']``. Message names ``agent-runner migrate`` so a
    rejected pre-0.2.12 config points straight at the fix."""
    if isinstance(value, str):
        raise ConfigError(
            f"{field}: must be a list, not a bare string {value!r}; run `agent-runner migrate`"
        )
    if not isinstance(value, list):
        raise ConfigError(
            f"{field}: must be a list, got {type(value).__name__}; run `agent-runner migrate`"
        )
    return [str(x) for x in value]


def _require_pct(value: Any, *, field: str) -> float:
    """Validate a TOML value is a percent in [0, 100]. Accepts int and float —
    TOML parses ``90`` as int and the shipped tuning tables recommend bare ints.
    Rejects bool (subclass of int, would silently coerce ``true`` -> 1.0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field}: must be a number, got {type(value).__name__} ({value!r})")
    v = float(value)
    # Keep the chained form: `v < 0 or v > 100` looks equivalent but admits nan,
    # which then disables the detector as silently as an out-of-range literal.
    if not 0.0 <= v <= 100.0:
        raise ConfigError(f"{field}: must be between 0 and 100, got {v}")
    return v


def _validate_remote_failure_tolerance(value: Any) -> int:
    """Validate monitor.remote_failure_tolerance_s: int in [0, 3600]."""
    v = _require_non_negative_int(value, field="monitor.remote_failure_tolerance_s")
    if v > 3600:
        raise ConfigError(f"monitor.remote_failure_tolerance_s: must be <= 3600, got {v}")
    return v


def _validate_regex_list(value: Any, *, field: str) -> list[str]:
    """Validate a list of regex pattern strings (each must compile). Returns the
    raw strings unchanged; callers compile when they need ``re.Pattern`` objects."""
    if not isinstance(value, list):
        raise ConfigError(f"{field}: expected a list of regex strings, got {type(value).__name__}")
    out: list[str] = []
    for p in value:
        if not isinstance(p, str):
            raise ConfigError(
                f"{field}: each pattern must be a string, got {type(p).__name__}: {p!r}"
            )
        try:
            re.compile(p)
        except re.error as e:
            raise ConfigError(f"{field}: invalid regex {p!r}: {e}") from e
        out.append(p)
    return out


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
# set (merged onto the base [agent] table before validation).
_AGENT_ALLOWED_FIELDS = frozenset(f.name for f in dataclasses.fields(AgentConfig))

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


def _parse_agent(agent_d: dict, *, field_prefix: str) -> AgentConfig:
    """Parse + validate an [agent] table (or a merged per-phase agent table).

    ``field_prefix`` names the table in error messages (e.g. ``"[agent]"`` or
    ``"[phases.b.agent]"``). The prompt_delivery validity check and the
    stdin/``{prompt}`` cross-check run here so a per-phase agent override is
    validated on the MERGED result, not just the base [agent] table.
    """
    prompt_delivery = str(agent_d.get("prompt_delivery", "argv"))
    prompt_arg_template = _require_str_list(
        _require(agent_d, "prompt_arg_template"), field=f"{field_prefix} prompt_arg_template"
    )
    if prompt_delivery not in _VALID_PROMPT_DELIVERY:
        raise ConfigError(
            f'invalid {field_prefix} prompt_delivery {prompt_delivery!r}: use "argv" or "stdin"'
        )
    if prompt_delivery == "stdin" and any("{prompt}" in a for a in prompt_arg_template):
        raise ConfigError(
            f"stdin delivery: remove {{prompt}} from {field_prefix} prompt_arg_template "
            "(the prompt is piped to stdin, not placed in argv)"
        )
    command = _require_str_list(_require(agent_d, "command"), field=f"{field_prefix} command")
    if not command:
        raise ConfigError(
            f"{field_prefix} command: must be a non-empty list; run `agent-runner migrate`"
        )
    return AgentConfig(
        command=command,
        prompt_arg_template=prompt_arg_template,
        name=agent_d.get("name"),
        env={str(k): str(v) for k, v in agent_d.get("env", {}).items()},
        prompt_delivery=prompt_delivery,  # type: ignore[arg-type]  # narrowed above
    )


def _parse_phase_overrides(
    phases_d: dict[str, Any],
    phases_list: list[str] | None,
    project_name: str,
    *,
    work_dir: Path,
    agent_d: dict[str, Any],
) -> dict[str, PhaseOverride]:
    """Parse [phases.<name>] sub-tables from raw TOML dict.

    Each sub-table is keyed by phase name (must appear in phases.list). Allowed
    fields are validated; unknown fields raise. ``agent_d`` is the base [agent]
    table — a per-phase ``agent`` sub-table field-merges onto it and the merged
    result is validated. Returns {phase_name: PhaseOverride}.
    """
    overrides: dict[str, PhaseOverride] = {}
    for key, value in phases_d.items():
        if key in ("list", "phase_policy"):
            continue
        if not isinstance(value, dict):
            continue
        phase_name = key
        if phases_list is None or phase_name not in phases_list:
            raise ConfigError(
                f"[phases.{phase_name}] declared but {phase_name!r} not in phases.list "
                f"({phases_list})"
            )
        unknown = set(value.keys()) - _PHASE_OVERRIDE_ALLOWED_FIELDS
        if unknown:
            raise ConfigError(
                f"unknown per-phase field(s) under [phases.{phase_name}]: {sorted(unknown)}; "
                f"allowed: round_timeout_s, disable_pre_round_hooks, prompt.files, "
                f"agent, runtime, schedule"
            )

        # runtime: flat aliases (round_timeout_s/disable_pre_round_hooks) and/or
        # a nested [phases.<name>.runtime] sub-table. Setting both twins raises.
        runtime_sub = value.get("runtime")
        if runtime_sub is not None and not isinstance(runtime_sub, dict):
            raise ConfigError(f"[phases.{phase_name}.runtime] must be a table")
        runtime_sub = runtime_sub or {}
        unknown_rt = set(runtime_sub.keys()) - _PHASE_RUNTIME_ALLOWED_FIELDS
        if unknown_rt:
            raise ConfigError(
                f"unknown field(s) under [phases.{phase_name}.runtime]: {sorted(unknown_rt)}; "
                f"allowed: round_timeout_s, disable_pre_round_hooks"
            )
        for fld in _PHASE_RUNTIME_ALLOWED_FIELDS:
            if fld in value and fld in runtime_sub:
                raise ConfigError(
                    f"[phases.{phase_name}] set both the flat field and "
                    f"[phases.{phase_name}.runtime].{fld}; use one"
                )

        # A field resolves from the nested [...runtime] sub-table, else the flat
        # alias (the twin case already raised above), so nested-first is safe.
        round_timeout_s = None
        if "round_timeout_s" in runtime_sub:
            round_timeout_s = _require_positive_int(
                runtime_sub["round_timeout_s"], field=f"phases.{phase_name}.runtime.round_timeout_s"
            )
        elif "round_timeout_s" in value:
            round_timeout_s = _require_positive_int(
                value["round_timeout_s"], field=f"phases.{phase_name}.round_timeout_s"
            )
        disable_hooks = None
        if "disable_pre_round_hooks" in runtime_sub:
            disable_hooks = _require_bool(
                runtime_sub["disable_pre_round_hooks"],
                field=f"phases.{phase_name}.runtime.disable_pre_round_hooks",
            )
        elif "disable_pre_round_hooks" in value:
            disable_hooks = _require_bool(
                value["disable_pre_round_hooks"],
                field=f"phases.{phase_name}.disable_pre_round_hooks",
            )

        prompt_files = None
        if "prompt" in value:
            prompt_sub = value["prompt"]
            if not isinstance(prompt_sub, dict) or "files" not in prompt_sub:
                raise ConfigError(f"[phases.{phase_name}].prompt must have a 'files' list")
            files_raw = _require_str_list(
                prompt_sub["files"], field=f"phases.{phase_name}.prompt.files"
            )
            prompt_files = [_expand_and_resolve(p, project_name, work_dir) for p in files_raw]

        phase_agent = None
        if "agent" in value:
            agent_sub = value["agent"]
            if not isinstance(agent_sub, dict):
                raise ConfigError(f"[phases.{phase_name}.agent] must be a table")
            unknown_agent = set(agent_sub.keys()) - _AGENT_ALLOWED_FIELDS
            if unknown_agent:
                raise ConfigError(
                    f"unknown field(s) under [phases.{phase_name}.agent]: "
                    f"{sorted(unknown_agent)}; allowed: {sorted(_AGENT_ALLOWED_FIELDS)}"
                )
            merged = {**agent_d, **agent_sub}
            phase_agent = _parse_agent(merged, field_prefix=f"[phases.{phase_name}.agent]")

        phase_schedule = None
        if "schedule" in value:
            sched_sub = value["schedule"]
            if not isinstance(sched_sub, dict):
                raise ConfigError(f"[phases.{phase_name}.schedule] must be a table")
            phase_schedule = _parse_schedule(sched_sub)

        overrides[phase_name] = PhaseOverride(
            round_timeout_s=round_timeout_s,
            disable_pre_round_hooks=disable_hooks,
            prompt_files=prompt_files,
            agent=phase_agent,
            schedule=phase_schedule,
        )
    return overrides


def _parse_substrate_fingerprint_paths(runtime_d: dict) -> list[str]:
    raw = runtime_d.get("substrate_fingerprint_paths", [])
    if not isinstance(raw, list):
        raise ConfigError("runtime.substrate_fingerprint_paths: must be list of glob strings")
    return [str(p) for p in raw]


def _parse_fresh_eyes_every_n(runtime_d: dict) -> int | None:
    raw = runtime_d.get("fresh_eyes_every_n")
    if raw is None:
        return None
    return _require_positive_int(raw, field="runtime.fresh_eyes_every_n")


_SCHEDULE_ALLOWED_FIELDS = frozenset({"timezone", "run_windows", "pause_windows"})


def _parse_schedule(schedule_d: dict) -> ScheduleConfig:
    unknown = set(schedule_d) - _SCHEDULE_ALLOWED_FIELDS
    if unknown:
        raise ConfigError(
            f"unknown [schedule] field(s): {sorted(unknown)}; "
            f"allowed: {sorted(_SCHEDULE_ALLOWED_FIELDS)}; run `agent-runner migrate`"
        )
    tz = schedule_d.get("timezone")
    if tz is not None:
        tz = str(tz)
        if not schedule.valid_timezone(tz):
            raise ConfigError(f"schedule.timezone: unknown IANA zone {tz!r}")

    def _parse_list(key: str) -> tuple[schedule.Window, ...]:
        raw = schedule_d.get(key, [])
        if not isinstance(raw, list):
            raise ConfigError(f"schedule.{key} must be a list of window strings")
        out = []
        for w in raw:
            try:
                out.append(schedule.parse_window(str(w)))
            except ValueError as e:
                raise ConfigError(f"schedule.{key}: {e}") from e
        return tuple(out)

    return ScheduleConfig(
        timezone=tz,
        run_windows=_parse_list("run_windows"),
        pause_windows=_parse_list("pause_windows"),
    )


def load_config(toml_path: Path) -> Config:
    if not toml_path.exists():
        raise FileNotFoundError(f"config not found: {toml_path}")
    with toml_path.open("rb") as f:
        raw = tomllib.load(f)

    agent_d = raw.get("agent", {})
    agent = _parse_agent(agent_d, field_prefix="[agent]")
    raw_work_dir = str(_require(raw, "runtime", "work_dir"))
    # A relative work_dir anchors to the config file's directory, not the loading
    # process's cwd — `--config /abs/proj/agent-runner.toml` must drive /abs/proj
    # no matter where the supervisor was launched from.
    work_dir = _expand_path(raw_work_dir, "")
    if not work_dir.is_absolute():
        work_dir = toml_path.parent / work_dir
    work_dir = work_dir.resolve()
    project_name = work_dir.name or "default"

    # Phases first — needed for per-phase round_timeout validation below.
    phases_d = raw.get("phases", {})
    phases_list = (
        _require_str_list(phases_d["list"], field="phases.list") if "list" in phases_d else None
    )
    phase_policy = str(phases_d.get("phase_policy", "wait"))
    if phase_policy not in ("wait", "skip"):
        raise ConfigError(
            f"[phases] phase_policy: {phase_policy!r} not in allowed values ['skip', 'wait']"
        )
    phases_overrides = _parse_phase_overrides(
        phases_d, phases_list, project_name, work_dir=work_dir, agent_d=agent_d
    )
    phases_cfg = PhasesConfig(
        list=phases_list,
        overrides=phases_overrides,
        phase_policy=phase_policy,  # type: ignore[arg-type]  # narrowed above
    )

    runtime_d = raw.get("runtime", {})
    if "round_timeout_per_phase" in runtime_d:
        raise ConfigError(
            "runtime.round_timeout_per_phase removed in 0.1.16; "
            "use [phases.<name>] round_timeout_s = X. Run `agent-runner migrate`."
        )

    if runtime_d.get("rate_limit_action") is not None:
        raise ConfigError(
            "runtime.rate_limit_action was removed in 0.1.29; use "
            "runtime.transient_error_action (same allowed values: "
            "back_off / skip / stop). Run `agent-runner migrate`."
        )

    transient_error_action_raw = runtime_d.get("transient_error_action")
    transient_error_action = str(
        transient_error_action_raw if transient_error_action_raw is not None else "back_off"
    )
    if transient_error_action not in _VALID_TRANSIENT_ERROR_ACTIONS:
        raise ConfigError(
            f"runtime.transient_error_action: {transient_error_action!r} not in allowed values "
            f"{sorted(_VALID_TRANSIENT_ERROR_ACTIONS)}"
        )

    runtime = RuntimeConfig(
        work_dir=work_dir,
        log_dir=_expand_and_resolve(
            str(_require(raw, "runtime", "log_dir")), project_name, work_dir
        ),
        round_timeout_s=_require_positive_int(
            runtime_d.get("round_timeout_s", 1800), field="runtime.round_timeout_s"
        ),
        restart_delay_s=_require_positive_int(
            runtime_d.get("restart_delay_s", 3), field="runtime.restart_delay_s"
        ),
        disable_pre_round_hooks=_require_bool(
            runtime_d.get("disable_pre_round_hooks", False),
            field="runtime.disable_pre_round_hooks",
        ),
        round_log_retention=_require_non_negative_int(
            runtime_d.get("round_log_retention", 0), field="runtime.round_log_retention"
        ),
        narrative_file=_expand_and_resolve(str(runtime_d["narrative_file"]), project_name, work_dir)
        if "narrative_file" in runtime_d
        else None,
        transient_error_action=transient_error_action,  # type: ignore[arg-type]
        max_rounds=_require_positive_int(runtime_d["max_rounds"], field="runtime.max_rounds")
        if "max_rounds" in runtime_d
        else None,
        stop_file=_expand_and_resolve(str(runtime_d["stop_file"]), project_name, work_dir)
        if "stop_file" in runtime_d
        else None,
        substrate_fingerprint_paths=_parse_substrate_fingerprint_paths(runtime_d),
        fresh_eyes_every_n=_parse_fresh_eyes_every_n(runtime_d),
        dry_run=_require_bool(
            runtime_d.get("dry_run", False),
            field="runtime.dry_run",
        ),
        max_grace_after_result_s=_require_non_negative_int(
            runtime_d.get("max_grace_after_result_s", 0),
            field="runtime.max_grace_after_result_s",
        ),
        grace_kill_ignore_patterns=_validate_regex_list(
            runtime_d.get("grace_kill_ignore_patterns", []),
            field="runtime.grace_kill_ignore_patterns",
        ),
    )
    prompt_d = raw.get("prompt", {})
    unknown_prompt = set(prompt_d) - _PROMPT_ALLOWED_FIELDS
    if unknown_prompt:
        raise ConfigError(
            f"unknown [prompt] field(s): {sorted(unknown_prompt)}; "
            f"allowed: {sorted(_PROMPT_ALLOWED_FIELDS)}; run `agent-runner migrate`"
        )
    mode = prompt_d.get("context_injection_mode", "prepend")
    if mode not in _VALID_INJECTION_MODES:
        raise ConfigError(
            f"prompt.context_injection_mode must be one of {sorted(_VALID_INJECTION_MODES)}, "
            f"got {mode!r}"
        )
    has_file = "file" in prompt_d
    has_files = "files" in prompt_d
    if has_file and has_files:
        raise ConfigError("set either prompt.file or prompt.files, not both")
    if not has_file and not has_files:
        raise ConfigError("missing required field: prompt.file or prompt.files")
    prompt_file = (
        _expand_and_resolve(str(prompt_d["file"]), project_name, work_dir) if has_file else None
    )
    if has_files:
        files_raw = _require_str_list(prompt_d["files"], field="prompt.files")
        if not files_raw:
            raise ConfigError(
                "prompt.files: must be a non-empty list (use a per-phase "
                "[phases.<name>.prompt] files = [] to disable prompts for a phase); "
                "run `agent-runner migrate`"
            )
        prompt_files = [_expand_and_resolve(p, project_name, work_dir) for p in files_raw]
    else:
        prompt_files = []
    prompt = PromptConfig(
        file=prompt_file,
        files=prompt_files,
        inject_context=_require_bool(
            prompt_d.get("inject_context", True), field="prompt.inject_context"
        ),
        context_injection_mode=mode,  # type: ignore[arg-type]  # narrowed by validation above
        concat_separator=str(prompt_d.get("concat_separator", "\n\n")),
        strip_yaml_frontmatter=_require_bool(
            prompt_d.get("strip_yaml_frontmatter", True),
            field="prompt.strip_yaml_frontmatter",
        ),
    )
    vcs_d = raw.get("vcs", {})
    if "orphan_action" in vcs_d:
        raise ConfigError(
            "vcs.orphan_action removed in 0.1.18; use vcs.dirty_action. Run `agent-runner migrate`."
        )
    dirty_action = str(vcs_d.get("dirty_action", "stash"))
    if dirty_action not in _VALID_DIRTY_ACTIONS:
        raise ConfigError(
            f"vcs.dirty_action: {dirty_action!r} not in allowed values "
            f"{{'stash', 'ignore', 'auto_commit'}}"
        )
    vcs = VcsConfig(
        stash_idempotency_s=_require_positive_int(
            vcs_d.get("stash_idempotency_s", 5), field="vcs.stash_idempotency_s"
        ),
        dirty_action=dirty_action,
    )
    monitor_d = raw.get("monitor", {})
    hh_d = monitor_d.get("host_health", {})
    host_health = MonitorHostHealthConfig(
        mem_avail_min_mb=_require_non_negative_int(
            hh_d.get("mem_avail_min_mb", 200),
            field="monitor.host_health.mem_avail_min_mb",
        ),
        disk_warning_pct=_require_pct(
            hh_d.get("disk_warning_pct", 90.0),
            field="monitor.host_health.disk_warning_pct",
        ),
        disk_critical_pct=_require_pct(
            hh_d.get("disk_critical_pct", 95.0),
            field="monitor.host_health.disk_critical_pct",
        ),
    )
    monitor = MonitorConfig(
        auth_fail_patterns=_validate_regex_list(
            monitor_d.get("auth_fail_patterns", _DEFAULT_AUTH_PATTERNS),
            field="monitor.auth_fail_patterns",
        ),
        auth_fail_hint=str(monitor_d.get("auth_fail_hint", _DEFAULT_AUTH_HINT)),
        auto_stop_on=(
            list(_DEFAULT_AUTO_STOP_ON)
            if "auto_stop_on" not in monitor_d
            else _require_str_list(monitor_d["auto_stop_on"], field="monitor.auto_stop_on")
        ),
        remote_failure_tolerance_s=_validate_remote_failure_tolerance(
            monitor_d.get("remote_failure_tolerance_s", _DEFAULT_REMOTE_FAILURE_TOLERANCE_S),
        ),
        anomaly_repetitive_window=_require_non_negative_int(
            monitor_d.get("anomaly_repetitive_window", 0),
            field="monitor.anomaly_repetitive_window",
        ),
        anomaly_repetitive_threshold=_require_non_negative_int(
            monitor_d.get("anomaly_repetitive_threshold", 0),
            field="monitor.anomaly_repetitive_threshold",
        ),
        host_health=host_health,
        round_progress_interval_s=_require_non_negative_int(
            monitor_d.get("round_progress_interval_s", 0),
            field="monitor.round_progress_interval_s",
        ),
        supervisor_stale_threshold_s=(
            None
            if monitor_d.get("supervisor_stale_threshold_s") is None
            else _require_non_negative_int(
                monitor_d["supervisor_stale_threshold_s"],
                field="monitor.supervisor_stale_threshold_s",
            )
        ),
    )
    if (
        monitor.anomaly_repetitive_threshold > 0
        and monitor.anomaly_repetitive_window > 0
        and monitor.anomaly_repetitive_threshold > monitor.anomaly_repetitive_window
    ):
        raise ConfigError(
            f"monitor.anomaly_repetitive_threshold ({monitor.anomaly_repetitive_threshold}) "
            f"must be <= anomaly_repetitive_window ({monitor.anomaly_repetitive_window}): "
            f"the detector can never fire otherwise; run `agent-runner migrate`"
        )
    plugins_raw = dict(raw.get("plugins") or {})  # copy so we can pop
    disable = (
        _require_str_list(plugins_raw.pop("disable"), field="plugins.disable")
        if "disable" in plugins_raw
        else []
    )
    plugins = PluginsConfig(disable=disable, raw=plugins_raw)

    schedule_cfg = _parse_schedule(raw.get("schedule", {}))

    cfg = Config(
        agent=agent,
        runtime=runtime,
        prompt=prompt,
        vcs=vcs,
        monitor=monitor,
        phases=phases_cfg,
        plugins=plugins,
        schedule=schedule_cfg,
    )

    # Honor [plugins] disable — must happen after registries are populated by
    # import-time plugin load. One-way operation; test isolation via isolating().
    if disable:
        from agent_runner import apply_plugin_disable

        apply_plugin_disable(disable)

    return cfg
