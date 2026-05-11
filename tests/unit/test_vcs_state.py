from __future__ import annotations

import subprocess
from pathlib import Path

from agent_runner.vcs_state import (
    StashRef,
    detect_dirty_files,
    drop_stash,
    is_git_repo,
    list_recent_stashes,
    pop_stash,
    set_diff_vs_head,
    stash_orphan,
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


def _make_dirty(repo: Path) -> None:
    (repo / "dirty.txt").write_text("uncommitted change\n")


def test_given_dirty_tree_when_stash_orphan_then_creates_marked_stash(tmp_git_repo: Path) -> None:
    _make_dirty(tmp_git_repo)
    ref = stash_orphan(tmp_git_repo, round_num=42, phase=None)
    assert ref is not None
    assert isinstance(ref, StashRef)
    assert ref.sha != ""
    assert ref.message.startswith("ORPHAN R42")
    assert detect_dirty_files(tmp_git_repo) == []  # tree clean after stash


def test_given_clean_tree_when_stash_orphan_then_returns_none(tmp_git_repo: Path) -> None:
    assert stash_orphan(tmp_git_repo, round_num=42, phase=None) is None


def test_given_recent_orphan_stash_when_stash_again_within_window_then_returns_existing_ref(
    tmp_git_repo: Path,
) -> None:
    _make_dirty(tmp_git_repo)
    first = stash_orphan(tmp_git_repo, round_num=42, phase=None, idempotency_s=5)
    assert first is not None
    _make_dirty(tmp_git_repo)
    second = stash_orphan(tmp_git_repo, round_num=42, phase=None, idempotency_s=5)
    assert second is not None
    assert second.sha == first.sha  # same ref returned, no new stash created


def test_given_phase_when_stash_orphan_then_message_includes_phase(tmp_git_repo: Path) -> None:
    _make_dirty(tmp_git_repo)
    ref = stash_orphan(tmp_git_repo, round_num=7, phase="diverge")
    assert ref is not None
    assert "phase=diverge" in ref.message


def test_given_stash_when_dropped_by_sha_then_no_longer_listed(tmp_git_repo: Path) -> None:
    _make_dirty(tmp_git_repo)
    ref = stash_orphan(tmp_git_repo, round_num=42, phase=None)
    assert ref is not None
    drop_stash(tmp_git_repo, ref.sha)
    assert ref.sha not in [s.sha for s in list_recent_stashes(tmp_git_repo)]


def test_given_stash_when_popped_by_sha_then_changes_restored(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "to-restore.txt").write_text("data\n")
    ref = stash_orphan(tmp_git_repo, round_num=42, phase=None)
    assert ref is not None
    assert not (tmp_git_repo / "to-restore.txt").exists()
    pop_stash(tmp_git_repo, ref.sha)
    assert (tmp_git_repo / "to-restore.txt").read_text() == "data\n"
