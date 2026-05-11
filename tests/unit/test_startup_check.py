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
