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


@dataclass(frozen=True)
class RuntimeConfig:
    work_dir: Path
    log_dir: Path
    round_timeout_s: int = 1800
    restart_delay_s: int = 3


@dataclass(frozen=True)
class PromptConfig:
    file: Path
    inject_context: bool = True
    context_injection_mode: Literal["prepend", "file", "none"] = "prepend"


@dataclass(frozen=True)
class VcsConfig:
    orphan_action: str = "stash"
    stash_idempotency_s: int = 5


# Default auth-failure detection regex — claude-aware. Migrated from monitor.py
# to config.py as the SSOT for the oauth_fail detector. Plugins / non-claude
# providers override via [monitor].auth_fail_patterns.
_DEFAULT_AUTH_PATTERNS: list[str] = [
    r"\b(oauth|unauthorized|401|api[_ ]key|"
    r"auth(entication)?[_ -]?(failed|error|expired)|session.*expired)\b",
]
_DEFAULT_AUTH_HINT: str = "Run `claude /login` on the supervisor host or refresh ANTHROPIC_API_KEY"


@dataclass(frozen=True)
class MonitorConfig:
    auth_fail_patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_AUTH_PATTERNS))
    auth_fail_hint: str = _DEFAULT_AUTH_HINT


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
    )
    raw_work_dir = str(_require(raw, "runtime", "work_dir"))
    work_dir = _expand_path(raw_work_dir, "").resolve()
    project_name = work_dir.name or "default"

    runtime_d = raw.get("runtime", {})
    runtime = RuntimeConfig(
        work_dir=work_dir,
        log_dir=_expand_path(str(_require(raw, "runtime", "log_dir")), project_name),
        round_timeout_s=int(runtime_d.get("round_timeout_s", 1800)),
        restart_delay_s=int(runtime_d.get("restart_delay_s", 3)),
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
        stash_idempotency_s=int(vcs_d.get("stash_idempotency_s", 5)),
    )
    monitor_d = raw.get("monitor", {})
    monitor = MonitorConfig(
        auth_fail_patterns=list(monitor_d.get("auth_fail_patterns", _DEFAULT_AUTH_PATTERNS)),
        auth_fail_hint=str(monitor_d.get("auth_fail_hint", _DEFAULT_AUTH_HINT)),
    )
    phases_d = raw.get("phases", {})
    phases = list(phases_d["list"]) if "list" in phases_d else None
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
