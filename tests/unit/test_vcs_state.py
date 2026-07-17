from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_runner.vcs_state import (
    _PLUGIN_OWNED_PATHS,
    AutoCommitError,
    StashRef,
    detect_dirty_files,
    drop_stash,
    is_git_repo,
    list_recent_stashes,
    pop_stash,
    stash_orphan,
    try_auto_commit,
)
from tests._test_helpers import isolating

_reset = isolating(_PLUGIN_OWNED_PATHS)


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


def test_given_no_registration_when_plugin_owned_paths_then_empty_list() -> None:
    from agent_runner.vcs_state import plugin_owned_paths

    assert plugin_owned_paths() == []


def test_given_paths_registered_when_plugin_owned_paths_then_snapshot_returned() -> None:
    from agent_runner.vcs_state import (
        plugin_owned_paths,
        register_plugin_owned_paths,
    )

    register_plugin_owned_paths(["proposals/", "reports/*.md"])
    assert plugin_owned_paths() == ["proposals/", "reports/*.md"]


def test_given_non_string_entry_when_register_then_raises_value_error() -> None:
    from agent_runner.vcs_state import register_plugin_owned_paths

    with pytest.raises(ValueError, match="non-string entry"):
        register_plugin_owned_paths(["ok.md", 42])  # type: ignore[list-item]


def test_given_trailing_slash_pattern_when_match_then_prefix_match() -> None:
    from agent_runner.vcs_state import (
        _matches_owned_path,
        register_plugin_owned_paths,
    )

    register_plugin_owned_paths(["proposals/"])
    assert _matches_owned_path("proposals/foo.md")
    assert _matches_owned_path("proposals/sub/bar.md")
    assert _matches_owned_path("proposals")
    assert not _matches_owned_path("proposalsX/foo.md")
    assert not _matches_owned_path("other/foo.md")


def test_given_glob_pattern_without_slash_when_match_then_purepath_semantics() -> None:
    from agent_runner.vcs_state import (
        _matches_owned_path,
        register_plugin_owned_paths,
    )

    register_plugin_owned_paths(["reports/*.md"])
    assert _matches_owned_path("reports/dev.md")
    # PurePath.match: single * does NOT cross slashes
    assert not _matches_owned_path("reports/sub/qa.md")


def test_given_recursive_glob_when_match_then_double_star_works() -> None:
    from agent_runner.vcs_state import (
        _matches_owned_path,
        register_plugin_owned_paths,
    )

    register_plugin_owned_paths(["logs/plugins/**/*"])
    assert _matches_owned_path("logs/plugins/argus/state.json")
    assert _matches_owned_path("logs/plugins/argus/deep/very/deep.txt")
    assert not _matches_owned_path("logs/other/state.json")


def test_given_multiple_patterns_when_match_then_any_matches() -> None:
    from agent_runner.vcs_state import (
        _matches_owned_path,
        register_plugin_owned_paths,
    )

    register_plugin_owned_paths(["proposals/", "reports/*.md"])
    assert _matches_owned_path("proposals/x.md")
    assert _matches_owned_path("reports/y.md")
    assert not _matches_owned_path("other/z.md")


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def test_given_only_log_dir_churn_when_auto_commit_then_git_head_unchanged(
    tmp_git_repo: Path,
) -> None:
    # b9: a zero-work round that only churned the runner's own bookkeeping
    # (lock/pid under log_dir) must NOT advance git_head.
    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    (log_dir / "agent-runner.lock").write_text("holder: pid 123\n")
    before = _head(tmp_git_repo)
    result = try_auto_commit(tmp_git_repo, 1, None, log_dir=log_dir)
    assert result == ""  # no-op: nothing staged after log_dir exclusion
    assert _head(tmp_git_repo) == before


def test_given_evolving_change_when_auto_commit_then_commits_but_not_log_dir(
    tmp_git_repo: Path,
) -> None:
    # b9: real work (.evolving, outside log_dir) still commits; the log_dir
    # bookkeeping churned alongside it is NOT committed.
    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    (log_dir / "agent-runner.lock").write_text("holder: pid 123\n")
    ev = tmp_git_repo / ".evolving" / "ticks"
    ev.mkdir(parents=True)
    (ev / "abc123def456").write_text('{"decision":"x"}\n')
    before = _head(tmp_git_repo)
    sha = try_auto_commit(tmp_git_repo, 2, None, log_dir=log_dir)
    assert sha and len(sha) >= 7  # commit SHA returned on success
    assert _head(tmp_git_repo) != before
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=tmp_git_repo, capture_output=True, text=True
    ).stdout
    assert ".evolving/ticks/abc123def456" in tracked
    assert "logs/agent-runner.lock" not in tracked


def test_given_log_dir_under_work_dir_when_stash_orphan_then_log_dir_preserved(
    tmp_git_repo: Path,
) -> None:
    # b9 (stash): git stash push -u must not sweep the runner's own log_dir.
    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    (log_dir / "agent-runner.lock").write_text("holder: pid 1\n")
    (tmp_git_repo / "work.py").write_text("x = 1\n")  # agent work (untracked)
    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None, log_dir=log_dir)
    assert ref is not None  # the agent's work WAS stashed
    assert not (tmp_git_repo / "work.py").exists()  # stashed away
    assert (log_dir / "agent-runner.lock").exists()  # NOT swept by stash -u


def test_given_dash_prefixed_gitignored_log_dir_when_stash_orphan_then_defense_runs(
    tmp_git_repo: Path,
) -> None:
    # The ignore gate must read a leading-dash log_dir as a pathname, not a switch.
    # Without a "--" separator git exits 129, which reads here as "not ignored", so
    # the ignored dir is named in the stash pathspec, `git stash push -u` is refused
    # rc=1, and stash_orphan returns None -- the orphan defense silently does not run
    # and the agent's work is left dirty for the next round to trip over.
    # Contents stay untracked: check-ignore honors the index, so a tracked file inside
    # would report "not ignored" with or without the separator and prove nothing.
    (tmp_git_repo / ".gitignore").write_text("-out/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_git_repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "ignore -out"],
        cwd=tmp_git_repo,
        check=True,
    )
    log_dir = tmp_git_repo / "-out"
    log_dir.mkdir()
    (log_dir / "agent-runner.lock").write_text("holder: pid 1\n")
    (tmp_git_repo / "work.py").write_text("x = 1\n")  # agent work (untracked)

    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None, log_dir=log_dir)

    assert ref is not None  # push was not refused: the defense ran
    assert not (tmp_git_repo / "work.py").exists()  # agent work stashed away
    assert (log_dir / "agent-runner.lock").exists()  # bookkeeping NOT swept


def test_given_only_log_dir_dirty_when_stash_orphan_then_noop_and_preserved(
    tmp_git_repo: Path,
) -> None:
    # A zero-work round that only churned log_dir: nothing to stash, logs survive.
    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    (log_dir / "events.jsonl").write_text("{}\n")
    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None, log_dir=log_dir)
    assert ref is None
    assert (log_dir / "events.jsonl").exists()


def test_try_auto_commit_returns_sha_on_success(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "work.py").write_text("x = 1\n")
    sha = try_auto_commit(tmp_git_repo, 1, None)
    assert sha and len(sha) >= 7
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_git_repo, capture_output=True, text=True
    ).stdout.strip()
    assert head.startswith(sha) or sha == head


def test_try_auto_commit_returns_empty_when_nothing_staged(tmp_git_repo: Path) -> None:
    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    (log_dir / "x.log").write_text("noise\n")  # only excluded bookkeeping
    assert try_auto_commit(tmp_git_repo, 1, None, log_dir=log_dir) == ""


def test_try_auto_commit_raises_on_git_failure(tmp_path: Path) -> None:
    with pytest.raises(AutoCommitError):
        try_auto_commit(tmp_path, 1, None)  # not a git repo
