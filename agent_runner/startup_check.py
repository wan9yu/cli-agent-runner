"""Boot-time precondition battery. R721 + #446 lesson — fail loud before
spawning the agent so we never silent-burn rounds on broken config.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agent_runner import agent_runtime
from agent_runner.config import AgentConfig, Config

ESCAPE_HATCH_ENV = "AGENT_RUNNER_SKIP_STARTUP_CHECK"

_MIN_PROMPT_BYTES = 500
_FORBIDDEN_FIRST_CHARS = frozenset({"-", " ", "\n", "\t", "\r"})


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    reason: str = ""
    how_to_fix: str = ""
    # PERMANENT (config-class, no retry) vs ENVIRONMENTAL (recoverable, retry) —
    # see battery_exit_code. Defaults to False (environmental) per the locked
    # decision "unclassified → environmental": a check nobody has explicitly
    # marked permanent is safer treated as retry-able than as a hard stop.
    permanent: bool = False


def _check_log_dir(cfg: Config) -> CheckResult:
    try:
        cfg.runtime.log_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.runtime.log_dir / ".write_probe"
        probe.write_text("x")
        probe.unlink()
        return CheckResult("log_dir_writable", True)
    except OSError as e:
        return CheckResult(
            "log_dir_writable",
            False,
            reason=f"cannot create or write {cfg.runtime.log_dir}: {e}",
            how_to_fix="chmod / chown the dir, or change runtime.log_dir in config",
        )


def _check_agent_target(agent: AgentConfig, work_dir: Path, name: str) -> CheckResult:
    if not agent.command:
        return CheckResult(name, False, "agent.command is empty", permanent=True)
    cli = agent.command[0]
    # Validate with the exact resolution the spawn uses (agent_runtime owns
    # the model): slash-containing commands resolve against work_dir (the
    # child's cwd); bare names use the CHILD's PATH ([agent.env] may set it).
    resolved = agent_runtime.resolve_exec_target(cli, work_dir, env_path=agent.env.get("PATH"))
    if resolved is None:
        relative = "/" in cli
        return CheckResult(
            name,
            False,
            reason=(
                f"{cli!r} not found or not executable under {work_dir}"
                if relative
                else f"{cli!r} not found on PATH"
            ),
            how_to_fix=(
                "fix the path relative to runtime.work_dir, or use an absolute path"
                if relative
                else f"install {cli} or set agent.command[0] to its absolute path"
            ),
            permanent=True,
        )
    return CheckResult(name, True)


def _check_work_dir_is_git(cfg: Config) -> CheckResult:
    from agent_runner.vcs_state import GitTimeout, is_git_repo

    try:
        is_repo = is_git_repo(cfg.runtime.work_dir)
    except GitTimeout as e:
        # Self-heals (a hung git process under host load, not a broken config)
        # -- degrade to a clean CheckResult like sibling _check_log_dir catches
        # OSError, instead of a raw traceback out of run_battery. permanent
        # defaults False (environmental): retry, don't give up for good.
        return CheckResult(
            "work_dir_is_git_repo",
            False,
            reason=f"git check on {cfg.runtime.work_dir} timed out: {e}",
            how_to_fix="investigate a hung git process or host load; retry",
        )
    if not is_repo:
        return CheckResult(
            "work_dir_is_git_repo",
            False,
            reason=f"{cfg.runtime.work_dir} is not a git working tree",
            how_to_fix="run `git init` in the work_dir, or change runtime.work_dir in config",
            permanent=True,
        )
    return CheckResult("work_dir_is_git_repo", True)


def _check_prompt_file(cfg: Config) -> CheckResult:
    targets: list = []
    if cfg.prompt.file is not None:
        targets.append(cfg.prompt.file)
    targets.extend(cfg.prompt.files)
    if not targets:
        return CheckResult(
            "prompt_file_exists",
            False,
            reason="no prompt files configured",
            how_to_fix="set prompt.file or prompt.files in agent-runner.toml",
            permanent=True,
        )
    first = targets[0]
    if not first.exists():
        return CheckResult(
            "prompt_file_exists",
            False,
            reason=f"{first} does not exist",
            how_to_fix="create the prompt .md file or fix prompt.file / prompt.files[0] in config",
            permanent=True,
        )
    return CheckResult("prompt_file_exists", True)


def _check_prompt_smoke(
    cfg: Config, *, phase: str | None = None, name: str = "prompt_smoke_passes"
) -> CheckResult:
    from agent_runner.api import assemble_prompt as _api_assemble_prompt

    try:
        prompt = _api_assemble_prompt(cfg, phase=phase, context=None)
    except Exception as e:
        return CheckResult(name, False, f"assembly failed: {e}", permanent=True)
    if not prompt:
        return CheckResult(name, False, "assembled prompt is empty", permanent=True)
    if prompt[0] in _FORBIDDEN_FIRST_CHARS:
        return CheckResult(
            name,
            False,
            reason=(
                f"first char {prompt[0]!r} is forbidden (R721 — agent CLI argv parsers "
                f"may reject leading dash/whitespace as a flag terminator)"
            ),
            how_to_fix="ensure the prompt body does not start with -, space, or newline",
            permanent=True,
        )
    if len(prompt.encode("utf-8")) < _MIN_PROMPT_BYTES:
        return CheckResult(
            name,
            False,
            reason=(f"prompt is {len(prompt.encode('utf-8'))} bytes < {_MIN_PROMPT_BYTES} minimum"),
            how_to_fix="add substantive content — a stub prompt suggests a broken config",
            permanent=True,
        )
    return CheckResult(name, True)


def _check_config_loaded(cfg: Config) -> CheckResult:
    # Already loaded if we're here; this slot exists to surface the check name in events.
    return CheckResult("config_loaded", True)


CHECKS: list[Callable[[Config], CheckResult]] = [
    _check_config_loaded,
    _check_log_dir,
    _check_work_dir_is_git,
    _check_prompt_file,
    _check_prompt_smoke,
]


def _agent_cli_checks(cfg: Config) -> list[CheckResult]:
    """Validate ``command[0]`` for EVERY profile the runner might launch — the
    base agent plus each phase's own agent — so a bad phase agent fails at boot
    instead of silent-burning the round it would have run. Each profile keeps
    its own env PATH (`[phases.<name>.agent].env` may differ from the base's).
    """
    # Base agent once, plus each phase that actually OVERRIDES the agent — a phase
    # with no [phases.<name>.agent] reuses the base command (already checked), so
    # re-validating it would just re-run resolve_exec_target on the identical target.
    phases = cfg.phases
    overriding = (
        [p for p in (phases.list or []) if (ov := phases.overrides.get(p)) and ov.agent]
        if phases is not None
        else []
    )
    results: list[CheckResult] = []
    for phase in [None, *overriding]:
        profile = cfg.profile_for(phase)
        name = "agent_cli_in_path" if phase is None else f"agent_cli_in_path:{phase}"
        results.append(_check_agent_target(profile.agent, cfg.runtime.work_dir, name))
    return results


def _phase_prompt_checks(cfg: Config) -> list[CheckResult]:
    """Smoke-check every phase that OVERRIDES the prompt (`[phases.<name>.prompt]`)
    — mirror of _agent_cli_checks onto prompts — so a broken phase prompt fails at
    boot instead of silent-burning the round it would run. Phases with no override
    (prompt_files is None) reuse the base prompt, already checked; an explicit
    `prompt.files = []` (a documented distinct state since 0.2.9) is preserved and
    not treated as broken here."""
    phases = cfg.phases
    if phases is None:
        return []
    results: list[CheckResult] = []
    for phase in phases.list or []:
        if cfg.profile_for(phase).prompt_files is None:
            continue
        results.append(_check_prompt_smoke(cfg, phase=phase, name=f"prompt_smoke_passes:{phase}"))
    return results


def run_battery(cfg: Config) -> list[CheckResult]:
    """Run all checks. Returns empty list if escape hatch env is set."""
    if os.environ.get(ESCAPE_HATCH_ENV, "").lower() in ("1", "true", "yes", "on"):
        return []
    return [check(cfg) for check in CHECKS] + _agent_cli_checks(cfg) + _phase_prompt_checks(cfg)


def battery_exit_code(failures: list[CheckResult]) -> int:
    """Map failing battery results to an exit code: any PERMANENT failure → 78
    (config_broken; systemd keeps the unit stopped); otherwise every failure is
    ENVIRONMENTAL → 76 (recoverable; serve retries at a fixed back-off until it heals).
    Permanent wins — a real config break is not masked by a concurrent disk blip."""
    from agent_runner.api import ENV_BATTERY_EXIT, PERMANENT_CONFIG_EXIT

    return PERMANENT_CONFIG_EXIT if any(f.permanent for f in failures) else ENV_BATTERY_EXIT
