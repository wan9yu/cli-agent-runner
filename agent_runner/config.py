"""TOML config loader with dataclass-based validation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


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


@dataclass(frozen=True)
class Config:
    agent: AgentConfig
    runtime: RuntimeConfig
    prompt: PromptConfig
    vcs: VcsConfig = field(default_factory=VcsConfig)
    phases: list[str] | None = None


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
        context_injection_mode=mode,  # type: ignore[arg-type]
    )
    vcs_d = raw.get("vcs", {})
    vcs = VcsConfig(
        orphan_action=str(vcs_d.get("orphan_action", "stash")),
        stash_idempotency_s=int(vcs_d.get("stash_idempotency_s", 5)),
    )
    phases_d = raw.get("phases", {})
    phases = list(phases_d["list"]) if "list" in phases_d else None

    return Config(agent=agent, runtime=runtime, prompt=prompt, vcs=vcs, phases=phases)
