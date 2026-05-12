"""End-to-end: `agent-runner init --preset {claude,aider}` produces valid scaffolds."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest


@pytest.mark.parametrize("preset_name", ["claude", "aider"])
def test_given_preset_when_init_then_toml_is_loadable(tmp_git_repo: Path, preset_name: str) -> None:
    from agent_runner.api import init
    from agent_runner.config import load_config

    result = init(tmp_git_repo, preset=preset_name, commit=False)
    assert result.preset == preset_name
    toml_path = tmp_git_repo / "agent-runner.toml"
    cfg = load_config(toml_path)
    assert cfg.agent.command


def test_given_aider_preset_when_init_then_aider_specific_fields_present(
    tmp_git_repo: Path,
) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset="aider", commit=False)
    text = (tmp_git_repo / "agent-runner.toml").read_text()
    parsed = tomllib.loads(text)
    assert parsed["agent"]["name"] == "aider"
    assert parsed["agent"]["command"][0] == "aider"
    # aider has no env injection
    assert "env" not in parsed["agent"]


def test_given_claude_preset_when_init_then_env_block_includes_autoupdater(
    tmp_git_repo: Path,
) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset="claude", commit=False)
    text = (tmp_git_repo / "agent-runner.toml").read_text()
    parsed = tomllib.loads(text)
    assert parsed["agent"]["env"]["DISABLE_AUTOUPDATER"] == "1"


def test_given_init_with_commit_when_preset_aider_then_commit_made(
    tmp_git_repo: Path,
) -> None:
    from agent_runner.api import init

    result = init(tmp_git_repo, preset="aider", commit=True)
    assert result.committed
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=tmp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert "agent-runner" in log


def test_given_existing_toml_when_init_without_force_then_raises(tmp_git_repo: Path) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset="claude", commit=False)
    with pytest.raises(FileExistsError):
        init(tmp_git_repo, preset="aider", commit=False)


def test_given_existing_toml_when_init_with_force_then_overwrites(tmp_git_repo: Path) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset="claude", commit=False)
    init(tmp_git_repo, preset="aider", force=True, commit=False)
    text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert 'command = ["aider"' in text
