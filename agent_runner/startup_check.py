"""Boot-time precondition battery. R721 + #446 lesson — fail loud before
spawning the agent so we never silent-burn rounds on broken config.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from agent_runner.config import Config
from agent_runner.prompt_loader import assemble_prompt

ESCAPE_HATCH_ENV = "AGENT_RUNNER_SKIP_STARTUP_CHECK"

_MIN_PROMPT_BYTES = 500
_FORBIDDEN_FIRST_CHARS = frozenset({"-", " ", "\n", "\t", "\r"})


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    reason: str = ""
    how_to_fix: str = ""


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


def _check_agent_cli(cfg: Config) -> CheckResult:
    if not cfg.agent.command:
        return CheckResult("agent_cli_in_path", False, "agent.command is empty")
    cli = cfg.agent.command[0]
    if shutil.which(cli) is None:
        return CheckResult(
            "agent_cli_in_path",
            False,
            reason=f"{cli!r} not found on PATH",
            how_to_fix=f"install {cli} or set agent.command[0] to its absolute path",
        )
    return CheckResult("agent_cli_in_path", True)


def _check_work_dir_is_git(cfg: Config) -> CheckResult:
    from agent_runner.vcs_state import is_git_repo
    if not is_git_repo(cfg.runtime.work_dir):
        return CheckResult(
            "work_dir_is_git_repo",
            False,
            reason=f"{cfg.runtime.work_dir} is not a git working tree",
            how_to_fix="run `git init` in the work_dir, or change runtime.work_dir in config",
        )
    return CheckResult("work_dir_is_git_repo", True)


def _check_prompt_file(cfg: Config) -> CheckResult:
    if not cfg.prompt.file.exists():
        return CheckResult(
            "prompt_file_exists",
            False,
            reason=f"{cfg.prompt.file} does not exist",
            how_to_fix="create the prompt .md file or fix prompt.file in config",
        )
    return CheckResult("prompt_file_exists", True)


def _check_prompt_smoke(cfg: Config) -> CheckResult:
    if not cfg.prompt.file.exists():
        return CheckResult(
            "prompt_smoke_passes",
            False,
            "prompt file missing — see prompt_file_exists",
        )
    try:
        prompt = assemble_prompt(cfg.prompt.file, context=None, inject_context=False)
    except Exception as e:
        return CheckResult("prompt_smoke_passes", False, f"assembly failed: {e}")
    if not prompt:
        return CheckResult("prompt_smoke_passes", False, "assembled prompt is empty")
    if prompt[0] in _FORBIDDEN_FIRST_CHARS:
        return CheckResult(
            "prompt_smoke_passes",
            False,
            reason=f"first char {prompt[0]!r} is forbidden (R721 — claude CLI rejects it)",
            how_to_fix="ensure the prompt body does not start with -, space, or newline",
        )
    if len(prompt.encode("utf-8")) < _MIN_PROMPT_BYTES:
        return CheckResult(
            "prompt_smoke_passes",
            False,
            reason=(
                f"prompt is {len(prompt.encode('utf-8'))} bytes "
                f"< {_MIN_PROMPT_BYTES} minimum"
            ),
            how_to_fix="add substantive content — a stub prompt suggests a broken config",
        )
    return CheckResult("prompt_smoke_passes", True)


def _check_config_loaded(cfg: Config) -> CheckResult:
    # Already loaded if we're here; this slot exists to surface the check name in events.
    return CheckResult("config_loaded", True)


CHECKS: list[Callable[[Config], CheckResult]] = [
    _check_config_loaded,
    _check_log_dir,
    _check_agent_cli,
    _check_work_dir_is_git,
    _check_prompt_file,
    _check_prompt_smoke,
]


def run_battery(cfg: Config) -> list[CheckResult]:
    """Run all checks. Returns empty list if escape hatch env is set."""
    if os.environ.get(ESCAPE_HATCH_ENV, "").lower() in ("1", "true", "yes", "on"):
        return []
    return [check(cfg) for check in CHECKS]
