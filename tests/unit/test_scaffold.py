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
