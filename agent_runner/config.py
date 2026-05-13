"""TOML config loader with dataclass-based validation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_VALID_INJECTION_MODES: frozenset[str] = frozenset({"prepend", "file", "none"})


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
    round_timeout_per_phase: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptConfig:
    file: Path
    inject_context: bool = True
    context_injection_mode: Literal["prepend", "file", "none"] = "prepend"


@dataclass(frozen=True)
class VcsConfig:
    orphan_action: str = "stash"
    stash_idempotency_s: int = 5


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


@dataclass(frozen=True)
class MonitorConfig:
    auth_fail_patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_AUTH_PATTERNS))
    auth_fail_hint: str = _DEFAULT_AUTH_HINT
    auto_stop_on: list[str] = field(default_factory=lambda: list(_DEFAULT_AUTO_STOP_ON))
    remote_failure_tolerance_s: int = 90


@dataclass(frozen=True)
class Config:
    agent: AgentConfig
    runtime: RuntimeConfig
    prompt: PromptConfig
    vcs: VcsConfig = field(default_factory=VcsConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    phases: list[str] | None = None
    plugins: dict[str, Any] | None = None


def _require(d: dict, *path: str) -> object:
    cur: object = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            raise ValueError(f"missing required field: {'.'.join(path)}")
        cur = cur[p]
    return cur


def _expand_path(s: str, project_name: str) -> Path:
    return Path(s.replace("{project}", project_name)).expanduser()


def _require_positive_int(value: Any, *, field: str) -> int:
    """Validate a TOML value is a positive int. Rejects bool (subclass of int
    in Python, would silently coerce e.g. ``true`` → 1) and any non-int."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field}: must be an integer, got {type(value).__name__} ({value!r})")
    if value <= 0:
        raise ValueError(f"{field}: must be positive, got {value}")
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


def _validate_round_timeout_per_phase_keys(
    per_phase: dict[str, int], phases: list[str] | None
) -> None:
    """All keys must appear in [phases] list (typo catcher)."""
    if not per_phase:
        return
    if phases is None:
        raise ValueError("runtime.round_timeout_per_phase requires [phases] list to be defined")
    unknown = set(per_phase) - set(phases)
    if unknown:
        raise ValueError(
            f"runtime.round_timeout_per_phase keys not in phases list: "
            f"{sorted(unknown)}; available phases: {phases}"
        )


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
    phases = list(phases_d["list"]) if "list" in phases_d else None

    runtime_d = raw.get("runtime", {})
    per_phase_raw = runtime_d.get("round_timeout_per_phase", {})
    per_phase: dict[str, int] = {
        str(k): _require_positive_int(v, field=f"runtime.round_timeout_per_phase[{str(k)!r}]")
        for k, v in per_phase_raw.items()
    }
    _validate_round_timeout_per_phase_keys(per_phase, phases)

    runtime = RuntimeConfig(
        work_dir=work_dir,
        log_dir=_expand_path(str(_require(raw, "runtime", "log_dir")), project_name),
        round_timeout_s=_require_positive_int(
            runtime_d.get("round_timeout_s", 1800), field="runtime.round_timeout_s"
        ),
        restart_delay_s=_require_positive_int(
            runtime_d.get("restart_delay_s", 3), field="runtime.restart_delay_s"
        ),
        round_timeout_per_phase=per_phase,
    )
    prompt_d = raw.get("prompt", {})
    mode = prompt_d.get("context_injection_mode", "prepend")
    if mode not in _VALID_INJECTION_MODES:
        raise ValueError(
            f"prompt.context_injection_mode must be one of {sorted(_VALID_INJECTION_MODES)}, "
            f"got {mode!r}"
        )
    prompt = PromptConfig(
        file=_expand_path(str(_require(prompt_d, "file")), project_name),
        inject_context=bool(prompt_d.get("inject_context", True)),
        context_injection_mode=mode,  # type: ignore[arg-type]  # narrowed by validation above
    )
    vcs_d = raw.get("vcs", {})
    vcs = VcsConfig(
        orphan_action=str(vcs_d.get("orphan_action", "stash")),
        stash_idempotency_s=_require_positive_int(
            vcs_d.get("stash_idempotency_s", 5), field="vcs.stash_idempotency_s"
        ),
    )
    monitor_d = raw.get("monitor", {})
    monitor = MonitorConfig(
        auth_fail_patterns=list(monitor_d.get("auth_fail_patterns", _DEFAULT_AUTH_PATTERNS)),
        auth_fail_hint=str(monitor_d.get("auth_fail_hint", _DEFAULT_AUTH_HINT)),
        auto_stop_on=list(monitor_d.get("auto_stop_on", _DEFAULT_AUTO_STOP_ON)),
        remote_failure_tolerance_s=_require_non_negative_int(
            monitor_d.get("remote_failure_tolerance_s", 90),
            field="monitor.remote_failure_tolerance_s",
        ),
    )
    plugins_d = raw.get("plugins")

    return Config(
        agent=agent,
        runtime=runtime,
        prompt=prompt,
        vcs=vcs,
        monitor=monitor,
        phases=phases,
        plugins=plugins_d,
    )
