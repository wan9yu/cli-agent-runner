"""``load_config``: reads an ``agent-runner.toml``, threads work_dir/project_name
resolution across the per-table parsers, and assembles the final ``Config``.

Thin glue by design — every table's own validation lives in ``parsers.py``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_runner.config.errors import ConfigError
from agent_runner.config.models import Config, PhasesConfig
from agent_runner.config.parsers import (
    _parse_agent,
    _parse_monitor,
    _parse_phase_overrides,
    _parse_plugins,
    _parse_prompt,
    _parse_runtime,
    _parse_schedule,
    _parse_vcs,
)
from agent_runner.config.validators import (
    _expand_path,
    _reject_control_chars,
    _require,
    _require_str_list,
    _require_table,
)


def load_config(toml_path: Path) -> Config:
    _reject_control_chars(str(toml_path), "config path")
    if not toml_path.exists():
        raise FileNotFoundError(f"config not found: {toml_path}")
    with toml_path.open("rb") as f:
        raw = tomllib.load(f)

    agent_d = _require_table(raw, "agent")
    agent = _parse_agent(agent_d, field_prefix="[agent]")
    runtime_d = _require_table(raw, "runtime")  # table-as-scalar guard before field lookups
    raw_work_dir = str(_require(raw, "runtime", "work_dir"))
    # Checked on the RAW string BEFORE any path expansion/resolution: a NUL
    # byte (a legal TOML basic-string escape) makes Path.resolve() raise a bare
    # ValueError, which would never reach the post-resolve check below and
    # so would never become a ConfigError (an unhandled traceback instead of
    # a clean exit-78 config error). The post-resolve check stays too, as
    # defense in depth.
    _reject_control_chars(raw_work_dir, "runtime.work_dir")
    # A relative work_dir anchors to the config file's directory, not the loading
    # process's cwd — `--config /abs/proj/agent-runner.toml` must drive /abs/proj
    # no matter where the supervisor was launched from.
    work_dir = _expand_path(raw_work_dir, "")
    if not work_dir.is_absolute():
        work_dir = toml_path.parent / work_dir
    work_dir = work_dir.resolve()
    _reject_control_chars(str(work_dir), "runtime.work_dir")
    project_name = work_dir.name or "default"

    # Phases first — needed for per-phase round_timeout validation below.
    phases_d = _require_table(raw, "phases")
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

    runtime = _parse_runtime(runtime_d, project_name=project_name, work_dir=work_dir)
    prompt = _parse_prompt(
        _require_table(raw, "prompt"), project_name=project_name, work_dir=work_dir
    )
    vcs = _parse_vcs(_require_table(raw, "vcs"))
    monitor = _parse_monitor(_require_table(raw, "monitor"))
    plugins = _parse_plugins(_require_table(raw, "plugins"))
    schedule_cfg = _parse_schedule(_require_table(raw, "schedule"))

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
    if plugins.disable:
        from agent_runner import apply_plugin_disable

        apply_plugin_disable(plugins.disable)

    return cfg
