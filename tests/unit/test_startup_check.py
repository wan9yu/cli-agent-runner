from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import (
    AgentConfig,
    Config,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.startup_check import CheckResult, run_battery  # noqa: F401


def _cfg(tmp_git_repo: Path, prompt_text: str = "Long prompt body for testing." * 20) -> Config:
    log_dir = tmp_git_repo / "logs"
    prompt_file = tmp_git_repo / "p.md"
    prompt_file.write_text(prompt_text)
    return Config(
        agent=AgentConfig(command=["bash"], prompt_arg_template=["-c", "{prompt}"]),
        runtime=RuntimeConfig(work_dir=tmp_git_repo, log_dir=log_dir),
        prompt=PromptConfig(file=prompt_file, inject_context=True),
        vcs=VcsConfig(),
        phases=None,
    )


def test_given_valid_config_when_battery_runs_then_all_checks_pass(tmp_git_repo: Path) -> None:
    results = run_battery(_cfg(tmp_git_repo))
    assert all(r.ok for r in results), [r for r in results if not r.ok]


def test_given_missing_prompt_file_when_battery_runs_then_prompt_check_fails(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo)
    cfg.prompt.file.unlink()
    results = run_battery(cfg)
    failed = [r for r in results if not r.ok]
    assert any(r.name == "prompt_file_exists" for r in failed)


def test_given_non_git_workdir_when_battery_runs_then_git_check_fails(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)  # tmp_path is NOT a git repo
    results = run_battery(cfg)
    failed = [r for r in results if not r.ok]
    assert any(r.name == "work_dir_is_git_repo" for r in failed)


def test_given_git_timeout_when_battery_runs_then_git_check_fails_clean(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GitTimeout out of is_git_repo (host under load / a hung git process)
    must degrade to a clean, environmental CheckResult -- like sibling
    _check_log_dir catches OSError -- instead of a raw traceback out of
    run_battery."""
    from agent_runner.vcs_state import GitTimeout

    def boom(_path):
        raise GitTimeout("git rev-parse --is-inside-work-tree exceeded 10s")

    monkeypatch.setattr("agent_runner.vcs_state.is_git_repo", boom)
    results = run_battery(_cfg(tmp_git_repo))
    failed = [r for r in results if not r.ok]
    git = next(r for r in failed if r.name == "work_dir_is_git_repo")
    assert "exceeded 10s" in git.reason
    assert git.permanent is False  # self-heals (hung git, not a broken config) -> environmental


def test_given_agent_cli_not_in_path_when_battery_runs_then_cli_check_fails(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo)
    object.__setattr__(cfg.agent, "command", ["definitely-nonexistent-cli-xyz"])
    results = run_battery(cfg)
    failed = [r for r in results if not r.ok]
    assert any(r.name == "agent_cli_in_path" for r in failed)


def test_given_prompt_starting_with_dash_when_battery_runs_then_smoke_check_fails(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo, prompt_text="-this-starts-with-dash" + "x" * 600)
    results = run_battery(cfg)
    failed = [r for r in results if not r.ok]
    assert any(r.name == "prompt_smoke_passes" for r in failed)


def test_given_prompt_under_min_bytes_when_battery_runs_then_smoke_check_fails(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo, prompt_text="too short")
    results = run_battery(cfg)
    failed = [r for r in results if not r.ok]
    assert any(r.name == "prompt_smoke_passes" for r in failed)


def test_given_prompt_with_yaml_frontmatter_when_battery_runs_then_smoke_passes(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo, prompt_text="---\ntitle: x\n---\n" + "Body content. " * 50)
    results = run_battery(cfg)
    failed = [r for r in results if not r.ok]
    assert not any(r.name == "prompt_smoke_passes" for r in failed)


def test_given_escape_hatch_env_set_when_battery_runs_then_returns_empty(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_RUNNER_SKIP_STARTUP_CHECK", "1")
    cfg = _cfg(tmp_git_repo)
    cfg.prompt.file.unlink()  # would normally fail
    results = run_battery(cfg)
    assert results == []


def test_given_relative_slash_command_when_battery_runs_then_validated_against_work_dir(
    tmp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """./relative agent commands exec in work_dir (cwd= spawn fix), so the CLI
    check must validate against work_dir — not the supervisor's cwd."""
    cfg = _cfg(tmp_git_repo)
    script = tmp_git_repo / "agent.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    object.__setattr__(cfg.agent, "command", ["./agent.sh"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # supervisor cwd deliberately != work_dir
    results = run_battery(cfg)
    cli = next(r for r in results if r.name == "agent_cli_in_path")
    assert cli.ok, cli.reason


def test_given_relative_slash_command_missing_in_work_dir_then_cli_check_fails(
    tmp_git_repo: Path,
) -> None:
    cfg = _cfg(tmp_git_repo)
    object.__setattr__(cfg.agent, "command", ["./no-such-agent.sh"])
    results = run_battery(cfg)
    cli = next(r for r in results if r.name == "agent_cli_in_path")
    assert not cli.ok
    assert str(tmp_git_repo) in cli.reason


def test_checkresult_permanent_defaults_false(tmp_git_repo: Path) -> None:
    # Unclassified checks are environmental by default (locked decision).
    assert CheckResult("x", ok=False, reason="r").permanent is False


def test_non_git_workdir_is_permanent(tmp_path: Path) -> None:
    failed = [r for r in run_battery(_cfg(tmp_path)) if not r.ok]
    git = next(r for r in failed if r.name == "work_dir_is_git_repo")
    assert git.permanent is True


def test_log_dir_write_failure_is_environmental(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "mkdir", boom)
    failed = [r for r in run_battery(_cfg(tmp_git_repo)) if not r.ok]
    log = next(r for r in failed if r.name == "log_dir_writable")
    assert log.permanent is False  # ENOSPC → recoverable → environmental


def test_broken_phase_prompt_override_fails_battery_by_name(tmp_git_repo: Path) -> None:
    from agent_runner.config import PhaseOverride, PhasesConfig

    cfg = _cfg(tmp_git_repo)
    missing = tmp_git_repo / "no-such-phase-prompt.md"
    object.__setattr__(
        cfg,
        "phases",
        PhasesConfig(list=["dev"], overrides={"dev": PhaseOverride(prompt_files=[missing])}),
    )
    failed = [r for r in run_battery(cfg) if not r.ok]
    dev = next(r for r in failed if r.name == "prompt_smoke_passes:dev")
    assert dev.permanent is True
