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
    cli = agent.spawn_command(work_dir)[0]
    # Validate with the exact resolution the spawn uses (agent_runtime owns
    # the model): slash-containing commands resolve against work_dir (the
    # child's cwd); bare names use the CHILD's PATH ([agent.env] may set it).
    resolved = agent_runtime.resolve_exec_target(cli, work_dir, env_path=agent.env.get("PATH"))
    if resolved is None:
        relative = "/" in cli
        # cli is exec_prefix[0] when a prefix is set (spawn_command splices the
        # prefix first) -- pointing the fix at agent.command[0] would be wrong.
        target = "[agent] exec_prefix[0]" if agent.exec_prefix else "agent.command[0]"
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
                else f"install {cli} or set {target} to its absolute path"
            ),
            permanent=True,
        )
    return CheckResult(name, True)


def _check_stdin_container_interactive(
    agent: AgentConfig, work_dir: Path, name: str
) -> CheckResult:
    """A stdin-delivery agent whose container run drops stdin (no -i/--interactive)
    silently hangs until the round-timeout kill -- fail loud at boot instead."""
    if agent.prompt_delivery != "stdin":
        return CheckResult(name, True)
    argv = agent.spawn_command(work_dir)
    if agent_runtime._detect_container_run(argv) is None:
        return CheckResult(name, True)
    # Whether docker forwards stdin depends ONLY on docker's own flags
    # (exec_prefix) -- agent.command runs INSIDE the container, so an -i/
    # --interactive there is irrelevant and must not satisfy this check.
    if agent_runtime._exec_prefix_is_interactive(agent.exec_prefix):
        return CheckResult(name, True)

    return CheckResult(
        name,
        False,
        reason="stdin prompt delivery into a container, but exec_prefix lacks -i/--interactive",
        how_to_fix=(
            'add "-i" to [agent] exec_prefix (docker drops stdin without it '
            "→ the agent hangs until the round-timeout kill)"
        ),
        permanent=True,
    )


def _check_control_plane_outside_container(
    agent: AgentConfig, work_dir: Path, log_dir: Path, name: str
) -> CheckResult:
    """The supervisor's per-round pid and lock-holder files live under
    log_dir. When the agent runs in a container and log_dir resolves inside
    work_dir -- which the container mounts -- the agent could forge them;
    refuse at boot. (The injected cidfile is kept safe separately, in a
    host-private temp dir outside any mount.) Enforced on work_dir (the mount
    we can reason about); exec_prefix's other mounts are opaque, so the
    general rule is documented, not code-enforced."""
    if agent_runtime._detect_container_run(agent.spawn_command(work_dir)) is None:
        return CheckResult(name, True)
    if log_dir.is_relative_to(work_dir):
        return CheckResult(
            name,
            False,
            reason="runtime.log_dir is inside work_dir, which the container mounts — "
            "the agent could forge the pid/lock control files agent-runner trusts",
            how_to_fix="set runtime.log_dir OUTSIDE work_dir (the default "
            "~/.agent-runner/<project>/logs already is); agent-runner's "
            "per-round pid and lock files live under log_dir, so keeping "
            "log_dir outside the mount keeps them out of the agent's reach",
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


def _phase_qualified(base: str, phase: str | None) -> str:
    """A check's name, suffixed `:<phase>` for an overriding phase profile."""
    return base if phase is None else f"{base}:{phase}"


@dataclass(frozen=True)
class CheckSpec:
    """One boot check. ``run`` is called per scope: base -> run(cfg);
    per_profile -> run(cfg, profile, phase); per_phase -> run(cfg, phase)."""

    kind: str
    scope: str  # "base" | "per_profile" | "per_phase"
    run: Callable[..., CheckResult]


_CHECK_SPECS: tuple[CheckSpec, ...] = (
    CheckSpec("config_loaded", "base", _check_config_loaded),
    CheckSpec("log_dir_writable", "base", _check_log_dir),
    CheckSpec("work_dir_is_git_repo", "base", _check_work_dir_is_git),
    CheckSpec("prompt_file_exists", "base", _check_prompt_file),
    CheckSpec("prompt_smoke_passes", "base", _check_prompt_smoke),
    CheckSpec(
        "agent_cli_in_path",
        "per_profile",
        lambda cfg, profile, phase: _check_agent_target(
            profile.agent,
            cfg.runtime.work_dir,
            _phase_qualified("agent_cli_in_path", phase),
        ),
    ),
    CheckSpec(
        "stdin_container_interactive",
        "per_profile",
        lambda cfg, profile, phase: _check_stdin_container_interactive(
            profile.agent,
            cfg.runtime.work_dir,
            _phase_qualified("stdin_container_interactive", phase),
        ),
    ),
    CheckSpec(
        "control_plane_outside_container",
        "per_profile",
        lambda cfg, profile, phase: _check_control_plane_outside_container(
            profile.agent,
            cfg.runtime.work_dir,
            cfg.runtime.log_dir,
            _phase_qualified("control_plane_outside_container", phase),
        ),
    ),
    CheckSpec(
        "prompt_smoke_passes",
        "per_phase",
        lambda cfg, phase: _check_prompt_smoke(
            cfg, phase=phase, name=_phase_qualified("prompt_smoke_passes", phase)
        ),
    ),
)


def _agent_override_phases(cfg: Config) -> list[str]:
    """Phases that actually OVERRIDE the agent — a phase with no
    ``[phases.<name>.agent]`` reuses the base command (already checked as the
    ``None`` profile), so re-validating it would just re-run
    ``resolve_exec_target`` on the identical target."""
    phases = cfg.phases
    if phases is None:
        return []
    return [p for p in (phases.list or []) if (ov := phases.overrides.get(p)) and ov.agent]


def _prompt_override_phases(cfg: Config) -> list[str]:
    """Phases that OVERRIDE the prompt (`[phases.<name>.prompt]`). Phases with
    no override (``prompt_files`` is ``None``) reuse the base prompt, already
    checked; an explicit ``prompt.files = []`` (a documented distinct state)
    is preserved and not treated as broken here."""
    phases = cfg.phases
    if phases is None:
        return []
    return [p for p in (phases.list or []) if cfg.profile_for(p).prompt_files is not None]


def all_check_kinds() -> tuple[str, ...]:
    """Distinct check kinds (``:<phase>`` suffix stripped), in battery order,
    derived from ``_CHECK_SPECS`` -- the single source of truth both
    ``run_battery`` and the defenses catalog's ``startup_smoke_check`` entry
    (see ``defenses.py``) read, so the human-readable count there can't
    hand-drift from what the battery actually emits; pinned against the real
    battery by ``test_all_check_kinds_should_match_battery_kinds_when_config_is_valid``.
    """
    seen: list[str] = []
    for spec in _CHECK_SPECS:
        if spec.kind not in seen:
            seen.append(spec.kind)
    return tuple(seen)


def run_battery(cfg: Config) -> list[CheckResult]:
    """Run all checks. Returns empty list if escape hatch env is set."""
    if os.environ.get(ESCAPE_HATCH_ENV, "").lower() in ("1", "true", "yes", "on"):
        return []
    results: list[CheckResult] = [s.run(cfg) for s in _CHECK_SPECS if s.scope == "base"]
    for phase in [None, *_agent_override_phases(cfg)]:
        profile = cfg.profile_for(phase)
        results += [s.run(cfg, profile, phase) for s in _CHECK_SPECS if s.scope == "per_profile"]
    for phase in _prompt_override_phases(cfg):
        results += [s.run(cfg, phase) for s in _CHECK_SPECS if s.scope == "per_phase"]
    return results


def battery_exit_code(failures: list[CheckResult]) -> int:
    """Map failing battery results to an exit code: any PERMANENT failure → 78
    (config_broken; systemd keeps the unit stopped); otherwise every failure is
    ENVIRONMENTAL → 76 (recoverable; serve retries at a fixed back-off until it heals).
    Permanent wins — a real config break is not masked by a concurrent disk blip."""
    from agent_runner.api import ENV_BATTERY_EXIT, PERMANENT_CONFIG_EXIT

    return PERMANENT_CONFIG_EXIT if any(f.permanent for f in failures) else ENV_BATTERY_EXIT
