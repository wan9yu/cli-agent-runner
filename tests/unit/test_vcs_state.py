from __future__ import annotations

import subprocess
from pathlib import Path

from agent_runner.vcs_state import (
    detect_dirty_files,
    is_git_repo,
    set_diff_vs_head,
)


def test_given_clean_tree_when_detect_dirty_then_returns_empty_list(tmp_git_repo: Path) -> None:
    assert detect_dirty_files(tmp_git_repo) == []


def test_given_modified_file_when_detect_dirty_then_returns_path(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("changed\n")
    assert detect_dirty_files(tmp_git_repo) == ["README.md"]


def test_given_untracked_file_when_detect_dirty_then_returns_path(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "new.txt").write_text("hi\n")
    assert "new.txt" in detect_dirty_files(tmp_git_repo)


def test_given_non_git_dir_when_is_git_repo_then_returns_false(tmp_path: Path) -> None:
    assert is_git_repo(tmp_path) is False


def test_given_git_repo_when_is_git_repo_then_returns_true(tmp_git_repo: Path) -> None:
    assert is_git_repo(tmp_git_repo) is True


def test_given_modified_file_when_set_diff_then_returns_lines_in_wt_not_in_head(
    tmp_git_repo: Path,
) -> None:
    p = tmp_git_repo / "README.md"
    p.write_text("init\nnew line\n")
    novel = set_diff_vs_head(tmp_git_repo, Path("README.md"))
    assert "new line" in novel
    assert "init" not in novel  # was already in HEAD


def test_given_missing_file_when_set_diff_then_returns_empty(
    tmp_git_repo: Path,
) -> None:
    novel = set_diff_vs_head(tmp_git_repo, Path("does-not-exist.md"))
    assert novel == set()


def test_given_renamed_file_when_detect_dirty_then_returns_new_path_only(
    tmp_git_repo: Path,
) -> None:
    # Create + commit a file, then rename via git mv (staged rename)
    (tmp_git_repo / "a.txt").write_text("data\n")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add a.txt"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "mv", "a.txt", "b.txt"], cwd=tmp_git_repo, check=True)
    dirty = detect_dirty_files(tmp_git_repo)
    assert "b.txt" in dirty
    assert "a.txt" not in dirty
    assert " -> " not in str(dirty)  # no malformed combined string
