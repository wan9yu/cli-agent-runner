from __future__ import annotations

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


def test_given_path_outside_repo_when_set_diff_then_raises_or_empty(tmp_git_repo: Path) -> None:
    novel = set_diff_vs_head(tmp_git_repo, Path("does-not-exist.md"))
    assert novel == set()
