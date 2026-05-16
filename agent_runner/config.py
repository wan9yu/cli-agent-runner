"""TOML config loader with dataclass-based validation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_VALID_INJECTION_MODES: frozenset[str] = frozenset({"prepend", "file", "none"})
_VALID_DIRTY_ACTIONS: frozenset[str] = frozenset({"stash", "ignore", "auto_commit"})
_VALID_RATE_LIMIT_ACTIONS: frozenset[str] = frozenset({"back_off", "skip", "stop"})


@dataclass(frozen=True)
class AgentConfig:
    command: list[str]
    prompt_arg_template: list[str]
    name: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeConfig:
    work_dir: Path
    log_dir: Path
    round_timeout_s: int = 1800
    restart_delay_s: int = 3
    disable_pre_round_hooks: bool = False
    round_log_retention: int = 100
    narrative_file: Path | None = None
    rate_limit_action: Literal["back_off", "skip", "stop"] = "back_off"
    max_rounds: int | None = None  # None = unbounded
    stop_file: Path | None = None  # None = disabled
    substrate_fingerprint_paths: list[str] = field(default_factory=list)
    fresh_eyes_every_n: int | None = None  # None = disabled


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


@dataclass(frozen=True)
class PhasesConfig:
    """Phases section: optional rotation list + per-phase override sub-tables.

    Replaces the old raw ``list[str] | None`` shape on ``Config.phases``. Code
    reading ``cfg.phases`` directly as a list must migrate to ``cfg.phases.list``.
    """

    list: list[str] | None = None
    overrides: dict[str, PhaseOverride] = field(default_factory=dict)


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

# Default window for tolerating transient remote failures before propagating.
# 0 = opt-out (immediate propagation).
_DEFAULT_REMOTE_FAILURE_TOLERANCE_S: int = 90


@dataclass(frozen=True)
class PluginsConfig:
    """Plugin-related TOML knobs.

    Migrating from free-form ``dict[str, Any] | None`` (0.1.11 and earlier) to a
    typed dataclass. Known keys are first-class fields; unknown keys land in
    ``.raw`` for forward-compatibility with plugin-author-defined `[plugins.*]`
    sub-keys (e.g. plugin packages may read their own config from `cfg.plugins.raw`).
    """

    disable: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitorConfig:
    auth_fail_patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_AUTH_PATTERNS))
    auth_fail_hint: str = _DEFAULT_AUTH_HINT
    auto_stop_on: list[str] = field(default_factory=lambda: list(_DEFAULT_AUTO_STOP_ON))
    remote_failure_tolerance_s: int = _DEFAULT_REMOTE_FAILURE_TOLERANCE_S


@dataclass(frozen=True)
class Config:
    agent: AgentConfig
    runtime: RuntimeConfig
    prompt: PromptConfig
    vcs: VcsConfig = field(default_factory=VcsConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    phases: PhasesConfig = field(default_factory=PhasesConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)


def _require(d: dict, *path: str) -> object:
    cur: object = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            raise ValueError(f"missing required field: {'.'.join(path)}")
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
        raise ValueError(f"{field}: must be an integer, got {type(value).__name__} ({value!r})")
    if value <= 0:
        raise ValueError(f"{field}: must be positive, got {value}")
    return value


def _require_bool(value: Any, *, field: str) -> bool:
    """Validate a TOML value is a bool. Distinct from int (in TOML, bool ≠ int)."""
    if not isinstance(value, bool):
        raise ValueError(f"{field}: must be a bool, got {type(value).__name__} ({value!r})")
    return value


def _require_non_negative_int(value: Any, *, field: str) -> int:
    """Validate a TOML value is a non-negative int (allows 0). Rejects bool
    and any non-int. Sibling of _require_positive_int where 0 has meaning
    (e.g. opt-out / disable)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field}: must be an integer, got {type(value).__name__} ({value!r})")
    if value < 0:
        raise ValueError(f"{field}: must be >= 0, got {value}")
    return value


def _validate_remote_failure_tolerance(value: Any) -> int:
    """Validate monitor.remote_failure_tolerance_s: int in [0, 3600]."""
    v = _require_non_negative_int(value, field="monitor.remote_failure_tolerance_s")
    if v > 3600:
        raise ValueError(f"monitor.remote_failure_tolerance_s: must be <= 3600, got {v}")
    return v


_PHASE_OVERRIDE_ALLOWED_FIELDS = frozenset(
    {
        "round_timeout_s",
        "disable_pre_round_hooks",
        "prompt",
    }
)


def _parse_phase_overrides(
    phases_d: dict[str, Any],
    phases_list: list[str] | None,
    project_name: str,
    *,
    work_dir: Path,
) -> dict[str, PhaseOverride]:
    """Parse [phases.<name>] sub-tables from raw TOML dict.

    Each sub-table is keyed by phase name (must appear in phases.list). Allowed
    fields are validated; unknown fields raise. Returns {phase_name: PhaseOverride}.
    """
    overrides: dict[str, PhaseOverride] = {}
    for key, value in phases_d.items():
        if key == "list":
            continue
        if not isinstance(value, dict):
            continue
        phase_name = key
        if phases_list is None or phase_name not in phases_list:
            raise ValueError(
                f"[phases.{phase_name}] declared but {phase_name!r} not in phases.list "
                f"({phases_list})"
            )
        unknown = set(value.keys()) - _PHASE_OVERRIDE_ALLOWED_FIELDS
        if unknown:
            raise ValueError(
                f"unknown per-phase field(s) under [phases.{phase_name}]: {sorted(unknown)}; "
                f"allowed: round_timeout_s, disable_pre_round_hooks, prompt.files"
            )
        round_timeout_s = (
            _require_positive_int(
                value["round_timeout_s"], field=f"phases.{phase_name}.round_timeout_s"
            )
            if "round_timeout_s" in value
            else None
        )
        disable_hooks = (
            _require_bool(
                value["disable_pre_round_hooks"],
                field=f"phases.{phase_name}.disable_pre_round_hooks",
            )
            if "disable_pre_round_hooks" in value
            else None
        )
        prompt_files = None
        if "prompt" in value:
            prompt_sub = value["prompt"]
            if not isinstance(prompt_sub, dict) or "files" not in prompt_sub:
                raise ValueError(f"[phases.{phase_name}].prompt must have a 'files' list")
            prompt_files = [
                _expand_and_resolve(str(p), project_name, work_dir) for p in prompt_sub["files"]
            ]
        overrides[phase_name] = PhaseOverride(
            round_timeout_s=round_timeout_s,
            disable_pre_round_hooks=disable_hooks,
            prompt_files=prompt_files,
        )
    return overrides


def _parse_substrate_fingerprint_paths(runtime_d: dict) -> list[str]:
    raw = runtime_d.get("substrate_fingerprint_paths", [])
    if not isinstance(raw, list):
        raise ValueError("runtime.substrate_fingerprint_paths: must be list of glob strings")
    return [str(p) for p in raw]


def _parse_fresh_eyes_every_n(runtime_d: dict) -> int | None:
    raw = runtime_d.get("fresh_eyes_every_n")
    if raw is None:
        return None
    return _require_positive_int(raw, field="runtime.fresh_eyes_every_n")


def load_config(toml_path: Path) -> Config:
    if not toml_path.exists():
        raise FileNotFoundError(f"config not found: {toml_path}")
    with toml_path.open("rb") as f:
        raw = tomllib.load(f)

    agent_d = raw.get("agent", {})
    agent = AgentConfig(
        command=list(_require(agent_d, "command")),
        prompt_arg_template=list(_require(agent_d, "prompt_arg_template")),
        name=agent_d.get("name"),
        env={str(k): str(v) for k, v in agent_d.get("env", {}).items()},
    )
    raw_work_dir = str(_require(raw, "runtime", "work_dir"))
    work_dir = _expand_path(raw_work_dir, "").resolve()
    project_name = work_dir.name or "default"

    # Phases first — needed for per-phase round_timeout validation below.
    phases_d = raw.get("phases", {})
    phases_list = list(phases_d["list"]) if "list" in phases_d else None
    phases_overrides = _parse_phase_overrides(
        phases_d, phases_list, project_name, work_dir=work_dir
    )
    phases_cfg = PhasesConfig(list=phases_list, overrides=phases_overrides)

    runtime_d = raw.get("runtime", {})
    if "round_timeout_per_phase" in runtime_d:
        raise ValueError(
            "runtime.round_timeout_per_phase removed in 0.1.16; "
            "use [phases.<name>] round_timeout_s = X — see docs/migrations/0.1.16.md"
        )

    rate_limit_action = str(runtime_d.get("rate_limit_action", "back_off"))
    if rate_limit_action not in _VALID_RATE_LIMIT_ACTIONS:
        raise ValueError(
            f"runtime.rate_limit_action: {rate_limit_action!r} not in allowed values "
            f"{sorted(_VALID_RATE_LIMIT_ACTIONS)}"
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
        round_log_retention=_require_positive_int(
            runtime_d.get("round_log_retention", 100), field="runtime.round_log_retention"
        ),
        narrative_file=_expand_and_resolve(str(runtime_d["narrative_file"]), project_name, work_dir)
        if "narrative_file" in runtime_d
        else None,
        rate_limit_action=rate_limit_action,  # type: ignore[arg-type]  # narrowed by validation
        max_rounds=_require_positive_int(runtime_d["max_rounds"], field="runtime.max_rounds")
        if "max_rounds" in runtime_d
        else None,
        stop_file=_expand_and_resolve(str(runtime_d["stop_file"]), project_name, work_dir)
        if "stop_file" in runtime_d
        else None,
        substrate_fingerprint_paths=_parse_substrate_fingerprint_paths(runtime_d),
        fresh_eyes_every_n=_parse_fresh_eyes_every_n(runtime_d),
    )
    prompt_d = raw.get("prompt", {})
    mode = prompt_d.get("context_injection_mode", "prepend")
    if mode not in _VALID_INJECTION_MODES:
        raise ValueError(
            f"prompt.context_injection_mode must be one of {sorted(_VALID_INJECTION_MODES)}, "
            f"got {mode!r}"
        )
    has_file = "file" in prompt_d
    has_files = "files" in prompt_d
    if has_file and has_files:
        raise ValueError("set either prompt.file or prompt.files, not both")
    if not has_file and not has_files:
        raise ValueError("missing required field: prompt.file or prompt.files")
    prompt_file = (
        _expand_and_resolve(str(prompt_d["file"]), project_name, work_dir) if has_file else None
    )
    prompt_files = (
        [_expand_and_resolve(str(p), project_name, work_dir) for p in prompt_d["files"]]
        if has_files
        else []
    )
    prompt = PromptConfig(
        file=prompt_file,
        files=prompt_files,
        inject_context=bool(prompt_d.get("inject_context", True)),
        context_injection_mode=mode,  # type: ignore[arg-type]  # narrowed by validation above
        concat_separator=str(prompt_d.get("concat_separator", "\n\n")),
        strip_yaml_frontmatter=_require_bool(
            prompt_d.get("strip_yaml_frontmatter", True),
            field="prompt.strip_yaml_frontmatter",
        ),
    )
    vcs_d = raw.get("vcs", {})
    if "orphan_action" in vcs_d:
        raise ValueError(
            "vcs.orphan_action removed in 0.1.18; use vcs.dirty_action — "
            "see docs/migrations/0.1.17.md"
        )
    dirty_action = str(vcs_d.get("dirty_action", "stash"))
    if dirty_action not in _VALID_DIRTY_ACTIONS:
        raise ValueError(
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
    monitor = MonitorConfig(
        auth_fail_patterns=list(monitor_d.get("auth_fail_patterns", _DEFAULT_AUTH_PATTERNS)),
        auth_fail_hint=str(monitor_d.get("auth_fail_hint", _DEFAULT_AUTH_HINT)),
        auto_stop_on=list(monitor_d.get("auto_stop_on", _DEFAULT_AUTO_STOP_ON)),
        remote_failure_tolerance_s=_validate_remote_failure_tolerance(
            monitor_d.get("remote_failure_tolerance_s", _DEFAULT_REMOTE_FAILURE_TOLERANCE_S),
        ),
    )
    plugins_raw = dict(raw.get("plugins") or {})  # copy so we can pop
    disable = list(plugins_raw.pop("disable", []))
    plugins = PluginsConfig(disable=disable, raw=plugins_raw)

    cfg = Config(
        agent=agent,
        runtime=runtime,
        prompt=prompt,
        vcs=vcs,
        monitor=monitor,
        phases=phases_cfg,
        plugins=plugins,
    )

    # Honor [plugins] disable — must happen after registries are populated by
    # import-time plugin load. One-way operation; test isolation via isolating().
    if disable:
        from agent_runner import apply_plugin_disable

        apply_plugin_disable(disable)

    return cfg
