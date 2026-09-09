from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_runner.scaffold import scaffold_project


def test_git_repo_should_create_three_files_when_scaffolded(tmp_git_repo: Path) -> None:
    result = scaffold_project(tmp_git_repo, force=False, commit=False)

    assert (tmp_git_repo / "agent-runner.toml").exists()
    assert (tmp_git_repo / "prompts" / "main.md").exists()
    assert (tmp_git_repo / ".gitignore").exists()
    assert {f.name for f in result.files_created} >= {"agent-runner.toml", "main.md", ".gitignore"}


def test_existing_toml_should_raise_when_scaffolded_without_force(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "agent-runner.toml").write_text("# old\n")

    with pytest.raises(FileExistsError):
        scaffold_project(tmp_git_repo, force=False, commit=False)


def test_existing_toml_should_be_overwritten_when_scaffolded_with_force(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "agent-runner.toml").write_text("# old\n")

    scaffold_project(tmp_git_repo, force=True, commit=False)

    assert "[agent]" in (tmp_git_repo / "agent-runner.toml").read_text()


def test_existing_gitignore_should_have_logs_appended_when_scaffolded(tmp_git_repo: Path) -> None:
    (tmp_git_repo / ".gitignore").write_text(".env\n")

    scaffold_project(tmp_git_repo, force=False, commit=False)

    text = (tmp_git_repo / ".gitignore").read_text()
    assert ".env" in text
    assert "logs/" in text


def test_gitignore_with_logs_already_present_should_not_be_duplicated_when_scaffolded(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / ".gitignore").write_text("logs/\n")

    scaffold_project(tmp_git_repo, force=False, commit=False)

    text = (tmp_git_repo / ".gitignore").read_text()
    assert text.count("logs/") == 1


def test_scaffold_should_create_git_commit_when_commit_true(tmp_git_repo: Path) -> None:
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


def test_non_git_dir_should_raise_when_scaffolded(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not a git"):
        scaffold_project(tmp_path, force=False, commit=False)


def test_aider_preset_should_write_aider_toml_when_scaffolded(
    tmp_git_repo: Path,
) -> None:
    result = scaffold_project(tmp_git_repo, preset="aider", force=False, commit=False)

    toml_text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert 'command = ["aider"' in toml_text
    assert "--yes-always" in toml_text
    assert "prompt_arg_template" in toml_text
    assert tmp_git_repo.name in toml_text  # {project} substituted
    assert "{project}" not in toml_text  # no unsubstituted placeholders
    assert result.work_dir == tmp_git_repo
    assert result.preset == "aider"


def test_unknown_preset_should_raise_when_scaffolded(tmp_git_repo: Path) -> None:
    with pytest.raises((FileNotFoundError, ValueError)):
        scaffold_project(tmp_git_repo, preset="nonexistent", force=False, commit=False)


def test_dirty_repo_should_commit_only_scaffold_files_when_scaffolded_with_commit(
    tmp_git_repo: Path,
) -> None:
    """`git add .` would sweep an unrelated working-tree change into the scaffold
    commit; `git add -- <files_created>` must commit only the scaffold files."""
    unrelated = tmp_git_repo / "unrelated.txt"
    unrelated.write_text("do not commit me\n")

    scaffold_project(tmp_git_repo, force=False, commit=True)

    committed = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=tmp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "unrelated.txt" not in committed
    assert "agent-runner.toml" in committed
    status = subprocess.run(
        ["git", "status", "--porcelain", "unrelated.txt"],
        cwd=tmp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "?? unrelated.txt" in status  # still untracked, untouched


def test_claude_preset_should_include_agent_env_block_when_scaffolded(
    tmp_git_repo: Path,
) -> None:
    """0.1.7: claude.toml carries [agent.env] DISABLE_AUTOUPDATER explicitly."""
    scaffold_project(tmp_git_repo, preset="claude", force=False, commit=False)

    toml_text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert "[agent.env]" in toml_text
    assert 'DISABLE_AUTOUPDATER = "1"' in toml_text
    assert 'CLAUDE_CODE_EFFORT_LEVEL = "xhigh"' in toml_text
