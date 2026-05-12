from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_runner.scaffold import scaffold_project


def test_given_git_repo_when_scaffold_then_creates_three_files(tmp_git_repo: Path) -> None:
    result = scaffold_project(tmp_git_repo, force=False, commit=False)
    assert (tmp_git_repo / "agent-runner.toml").exists()
    assert (tmp_git_repo / "prompts" / "main.md").exists()
    assert (tmp_git_repo / ".gitignore").exists()
    assert {f.name for f in result.files_created} >= {"agent-runner.toml", "main.md", ".gitignore"}


def test_given_existing_toml_no_force_when_scaffold_then_raises(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "agent-runner.toml").write_text("# old\n")
    with pytest.raises(FileExistsError):
        scaffold_project(tmp_git_repo, force=False, commit=False)


def test_given_existing_toml_with_force_when_scaffold_then_overwrites(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "agent-runner.toml").write_text("# old\n")
    scaffold_project(tmp_git_repo, force=True, commit=False)
    assert "[agent]" in (tmp_git_repo / "agent-runner.toml").read_text()


def test_given_existing_gitignore_when_scaffold_then_appends_logs(tmp_git_repo: Path) -> None:
    (tmp_git_repo / ".gitignore").write_text(".env\n")
    scaffold_project(tmp_git_repo, force=False, commit=False)
    text = (tmp_git_repo / ".gitignore").read_text()
    assert ".env" in text
    assert "logs/" in text


def test_given_gitignore_already_has_logs_when_scaffold_then_no_duplicate(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / ".gitignore").write_text("logs/\n")
    scaffold_project(tmp_git_repo, force=False, commit=False)
    text = (tmp_git_repo / ".gitignore").read_text()
    assert text.count("logs/") == 1


def test_given_commit_true_when_scaffold_then_creates_git_commit(tmp_git_repo: Path) -> None:
    result = scaffold_project(tmp_git_repo, force=False, commit=True)
    assert result.committed is True
    log = subprocess.run(
        ["git", "log", "--format=%s", "-1"],
        cwd=tmp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert "agent-runner" in log


def test_given_non_git_dir_when_scaffold_then_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not a git"):
        scaffold_project(tmp_path, force=False, commit=False)


def test_given_aider_preset_when_scaffold_then_aider_toml_written(
    tmp_git_repo: Path,
) -> None:
    from agent_runner.scaffold import scaffold_project

    result = scaffold_project(tmp_git_repo, preset="aider", force=False, commit=False)
    toml_text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert 'command = ["aider"' in toml_text
    assert "--yes-always" in toml_text
    assert "prompt_arg_template" in toml_text
    assert tmp_git_repo.name in toml_text  # {project} substituted
    assert "{project}" not in toml_text  # no unsubstituted placeholders
    assert result.work_dir == tmp_git_repo
    assert result.preset == "aider"


def test_given_unknown_preset_when_scaffold_then_raises(tmp_git_repo: Path) -> None:
    import pytest

    from agent_runner.scaffold import scaffold_project

    with pytest.raises((FileNotFoundError, ValueError)):
        scaffold_project(tmp_git_repo, preset="nonexistent", force=False, commit=False)


def test_given_claude_preset_when_scaffold_then_includes_agent_env_block(
    tmp_git_repo: Path,
) -> None:
    """0.1.7: claude.toml carries [agent.env] DISABLE_AUTOUPDATER explicitly."""
    from agent_runner.scaffold import scaffold_project

    scaffold_project(tmp_git_repo, preset="claude", force=False, commit=False)
    toml_text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert "[agent.env]" in toml_text
    assert 'DISABLE_AUTOUPDATER = "1"' in toml_text
    assert 'CLAUDE_CODE_EFFORT_LEVEL = "xhigh"' in toml_text
