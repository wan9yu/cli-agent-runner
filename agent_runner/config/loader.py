"""``load_config``: reads an ``agent-runner.toml``, threads work_dir/project_name
resolution across the per-table parsers, and assembles the final ``Config``.

Thin glue by design — every table's own validation lives in ``parsers.py``.
"""

from __future__ import annotations

import tomllib
import warnings
from pathlib import Path

from agent_runner.config.errors import ConfigError
from agent_runner.config.models import Config, PhasesConfig
from agent_runner.config.parsers import (
    _parse_agent,
    _parse_goal,
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

_CURRENT_SCHEMA_VERSION = 1


def _reject_static_resume_flag(cfg) -> None:
    """A resume-capable preset must not already carry its resume_flag token in
    the static command/prompt_arg_template -- agent-runner injects [flag, id]
    per round, so a static copy would duplicate it. Boot refusal (ConfigError)
    keeps best-effort-never-error at round time. Runs AFTER
    load_and_register_plugins so resolve_resume_flag can see the manifests
    (it is registry-backed, unlike the boot-time {prompt} guard)."""
    from agent_runner._plugin_manifest import resolve_resume_flag

    agents = [(None, cfg.agent)] + [
        (name, ov.agent) for name, ov in cfg.phases.overrides.items() if ov.agent is not None
    ]
    for phase_name, a in agents:
        flag = resolve_resume_flag(a.binary)
        if flag is None:
            continue
        if flag in a.command or flag in a.prompt_arg_template:
            where = "[agent]" if phase_name is None else f"[phases.{phase_name}.agent]"
            raise ConfigError(
                f"{where} command already contains {flag!r}: agent-runner injects it "
                "per round for cross-round resume; remove it from the static command"
            )


def _reject_goal_ledger_not_listed(cfg: Config) -> None:
    """A [goal].ledger doesn't exist at cold start -- it's written by the agent
    mid-run -- so it must ride in [prompt] files at index >= 1: index 0 is a
    fatal-on-missing read (prompt_loader.py), and the single `file=` form can
    never carry a second entry at all. Checks every configured phase's own
    effective files list, mirroring Config.profile_for's override-or-base
    resolution rule for prompt_files (no override -> the base [prompt] table)."""
    if cfg.goal is None:
        return
    ledger = cfg.goal.ledger
    phase_names: list[str | None] = list(cfg.phases.list) if cfg.phases.list else [None]
    for phase_name in phase_names:
        where = "[prompt]" if phase_name is None else f"[phases.{phase_name}.prompt]"
        prompt_files = cfg.profile_for(phase_name).prompt_files
        # No override -> [] via cfg.prompt.files, which is already [] when the
        # single `file=` form is in play (parser-enforced): never carries a
        # second entry.
        files = (
            [str(p) for p in prompt_files]
            if prompt_files is not None
            else [str(p) for p in cfg.prompt.files]
        )
        if ledger not in files or files.index(ledger) == 0:
            raise ConfigError(
                f"{where}: [goal] ledger {ledger!r} is not listed in the resolved "
                "prompt files at index >= 1 (the single `file=` form can't carry it, "
                f"and index 0 doesn't exist at cold start); list {ledger!r} in "
                "[prompt] files at index >= 1"
            )


def _reject_goal_ledger_in_stash_swept_tree(cfg: Config) -> None:
    """The ``[goal].ledger`` is supervisor-written and must PERSIST across
    rounds, but the round's own vcs step sweeps the work tree: under the
    DEFAULT ``[vcs] dirty_action="stash"`` a ``git stash push -u`` stashes
    every dirty path EXCEPT ``log_dir`` (see ``vcs_state``'s exclude pathspec).
    A ledger resolved INSIDE ``work_dir`` but OUTSIDE ``log_dir`` would be
    stashed away as orphan work right after the round it fired for -- the steer
    would silently last one round.

    Pure path arithmetic (no git): require the resolved ledger to sit OUTSIDE
    ``work_dir``, OR UNDER ``log_dir`` (the one work-tree path the stash
    pathspec excludes). Composes with -- and runs after --
    ``_reject_goal_ledger_not_listed``."""
    if cfg.goal is None:
        return
    # .resolve() all three: an absolute log_dir (or ledger) is NOT canonicalized
    # by _expand_and_resolve (only a relative path is), so a symlinked absolute
    # log_dir (e.g. /tmp -> /private/tmp on macOS) would otherwise FALSE-REJECT
    # a ledger genuinely under it -- work_dir is always canonical already
    # (loader.load_config resolves it unconditionally), so this is a no-op there.
    ledger = Path(cfg.goal.ledger).resolve()
    work_dir = cfg.runtime.work_dir.resolve()
    log_dir = cfg.runtime.log_dir.resolve()
    if ledger.is_relative_to(work_dir) and not ledger.is_relative_to(log_dir):
        raise ConfigError(
            f"[goal] ledger {cfg.goal.ledger!r} resolves inside runtime.work_dir "
            f"({work_dir}) but outside runtime.log_dir ({log_dir}): the default "
            '[vcs] dirty_action="stash" would stash it away as orphan work after '
            "the round it fired for, so the steer would last only one round. Put "
            'the ledger under log_dir (e.g. ledger = "logs/lessons.md") or at an '
            "absolute path outside work_dir."
        )


def _warn_goal_checks_budget_disarms_fast_spin(cfg: Config) -> None:
    """A goal check's wall time counts inside ``round_duration_s``, the
    discriminator for serve's fast-spin give-up breakers (``crash_loop`` counts
    a crash only under ``CRASH_LOOP_SHORT_EXIT_S``, ``stalled_no_progress`` only
    under ``_NO_PROGRESS_SHORT_S``). A goal-check budget at or above that short
    window can push an otherwise fast-crashing round past the threshold and
    DISARM the breaker. The goal loop still never CAUSES a give-up -- the
    firewall keeps the kill/give-up path blind to ``goal_*`` events -- so this
    is the one place a long check can only DELAY (never trigger) one. A
    legitimate ``pytest`` goal-check may well exceed the window, so this WARNS
    (via the same load-time ``warnings`` channel plugin-load failures use)
    rather than rejecting."""
    if cfg.goal is None:
        return
    # Function-scope import: config/ must not import _serve_policy at module
    # scope (_serve_policy imports agent_runner.config for ConfigError -- a
    # cycle). By load_config-call time the module is importable.
    from agent_runner._serve_policy import _NO_PROGRESS_SHORT_S

    allowance = cfg.goal.checks_allowance_s
    if allowance >= _NO_PROGRESS_SHORT_S:
        warnings.warn(
            f"[goal] total goal-check budget is {allowance}s, at or above the "
            f"{_NO_PROGRESS_SHORT_S}s fast-spin give-up window: a goal check's wall "
            "time counts toward the round's own duration, so a long check can delay "
            "(never trigger) the stalled_no_progress/crash_loop breakers. Keep total "
            "check time under that window if you rely on them.",
            stacklevel=2,
        )


def _check_schema_version(raw: dict) -> None:
    """Boot gate: a config's ``schema_version`` decouples the on-disk config
    shape from the installed package version, so a config can be migrated
    once and stay loadable across many package releases. Absent or behind
    the version this build expects means the config was never migrated;
    ahead means an operator downgraded the package under a newer config."""
    version = raw.get("schema_version")
    if version is None:
        raise ConfigError("config predates schema_version — run 'agent-runner migrate'")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConfigError(f"schema_version must be an integer, got {version!r}")
    if version > _CURRENT_SCHEMA_VERSION:
        raise ConfigError(
            f"config schema_version {version} is newer than this agent-runner "
            f"supports (max {_CURRENT_SCHEMA_VERSION}) — upgrade agent-runner"
        )
    if version < _CURRENT_SCHEMA_VERSION:
        raise ConfigError(
            f"config schema_version {version} is older than this agent-runner "
            "supports — run 'agent-runner migrate'"
        )


def load_config(toml_path: Path) -> Config:
    _reject_control_chars(str(toml_path), "config path")
    if not toml_path.exists():
        raise FileNotFoundError(f"config not found: {toml_path}")
    with toml_path.open("rb") as f:
        raw = tomllib.load(f)

    _check_schema_version(raw)

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

    # Phases first — needed for per-phase round_budget validation below.
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
    goal = (
        _parse_goal(_require_table(raw, "goal"), project_name=project_name, work_dir=work_dir)
        if "goal" in raw
        else None
    )

    cfg = Config(
        agent=agent,
        runtime=runtime,
        prompt=prompt,
        vcs=vcs,
        monitor=monitor,
        phases=phases_cfg,
        plugins=plugins,
        schedule=schedule_cfg,
        goal=goal,
    )

    # Verify + import discovered agent_runner.plugins entry points now that
    # [plugins] is parsed -- gating the import on config, which package-import
    # time never could. Then honor disable (belt-and-suspenders unregister).
    from agent_runner import apply_plugin_disable, load_and_register_plugins

    load_and_register_plugins(plugins, log_dir=cfg.runtime.log_dir)
    if plugins.disable:
        apply_plugin_disable(plugins.disable)

    _reject_static_resume_flag(cfg)
    _reject_goal_ledger_not_listed(cfg)
    _reject_goal_ledger_in_stash_swept_tree(cfg)
    _warn_goal_checks_budget_disarms_fast_spin(cfg)

    return cfg
