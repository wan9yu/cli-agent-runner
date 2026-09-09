"""End-to-end: `agent-runner init --preset <name>` produces valid scaffolds (all presets)."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from tests._test_helpers import PRESET_NAMES


@pytest.mark.parametrize("preset_name", PRESET_NAMES)
def test_preset_should_produce_loadable_toml_when_initialized(
    tmp_git_repo: Path, preset_name: str
) -> None:
    from agent_runner.api import init
    from agent_runner.config import load_config

    result = init(tmp_git_repo, preset=preset_name, commit=False)

    assert result.preset == preset_name
    toml_path = tmp_git_repo / "agent-runner.toml"
    cfg = load_config(toml_path)
    assert cfg.agent.command


def test_aider_preset_should_include_aider_specific_fields_when_initialized(
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


def test_claude_preset_should_disable_autoupdater_via_env_when_initialized(
    tmp_git_repo: Path,
) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset="claude", commit=False)

    text = (tmp_git_repo / "agent-runner.toml").read_text()
    parsed = tomllib.loads(text)
    assert parsed["agent"]["env"]["DISABLE_AUTOUPDATER"] == "1"


def test_init_should_commit_when_commit_flag_true(
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


def test_init_should_raise_when_toml_exists_without_force(tmp_git_repo: Path) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset="claude", commit=False)

    with pytest.raises(FileExistsError):
        init(tmp_git_repo, preset="aider", commit=False)


def test_init_should_overwrite_when_toml_exists_with_force(tmp_git_repo: Path) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset="claude", commit=False)

    init(tmp_git_repo, preset="aider", force=True, commit=False)

    text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert 'command = ["aider"' in text


def test_gemini_preset_should_include_gemini_specific_fields_when_initialized(
    tmp_git_repo: Path,
) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset="gemini", commit=False)

    text = (tmp_git_repo / "agent-runner.toml").read_text()
    parsed = tomllib.loads(text)
    assert parsed["agent"]["name"] == "gemini"
    assert "gemini" in parsed["agent"]["command"]
    assert "--yolo" in parsed["agent"]["command"]
