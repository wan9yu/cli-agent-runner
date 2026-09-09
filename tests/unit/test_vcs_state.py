from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_runner.vcs_state import (
    _PLUGIN_OWNED_PATHS,
    AutoCommitError,
    StashRef,
    detect_dirty_files,
    is_git_repo,
    stash_orphan,
    try_auto_commit,
)
from tests._test_helpers import isolating

_reset = isolating(_PLUGIN_OWNED_PATHS)


def test_detect_dirty_files_should_return_empty_list_when_tree_clean(tmp_git_repo: Path) -> None:
    assert detect_dirty_files(tmp_git_repo) == []


def test_detect_dirty_files_should_return_path_when_file_modified(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("changed\n")

    assert detect_dirty_files(tmp_git_repo) == ["README.md"]


def test_detect_dirty_files_should_return_path_when_file_untracked(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "new.txt").write_text("hi\n")

    assert "new.txt" in detect_dirty_files(tmp_git_repo)


def test_is_git_repo_should_return_false_when_dir_not_git(tmp_path: Path) -> None:
    assert is_git_repo(tmp_path) is False


def test_is_git_repo_should_return_true_when_dir_is_git_repo(tmp_git_repo: Path) -> None:
    assert is_git_repo(tmp_git_repo) is True


def test_detect_dirty_files_should_return_new_path_only_when_file_renamed(
    tmp_git_repo: Path,
) -> None:
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


def test_stash_orphan_should_create_marked_stash_when_tree_dirty(tmp_git_repo: Path) -> None:
    _make_dirty(tmp_git_repo)

    ref = stash_orphan(tmp_git_repo, round_num=42, phase=None)

    assert ref is not None
    assert isinstance(ref, StashRef)
    assert ref.sha != ""
    assert ref.message.startswith("ORPHAN R42")
    assert detect_dirty_files(tmp_git_repo) == []  # tree clean after stash


def test_stash_orphan_should_return_none_when_tree_clean(tmp_git_repo: Path) -> None:
    assert stash_orphan(tmp_git_repo, round_num=42, phase=None) is None


def test_stash_orphan_should_return_existing_ref_when_called_again_within_idempotency_window(
    tmp_git_repo: Path,
) -> None:
    _make_dirty(tmp_git_repo)
    first = stash_orphan(tmp_git_repo, round_num=42, phase=None, idempotency_s=5)
    assert first is not None

    _make_dirty(tmp_git_repo)
    second = stash_orphan(tmp_git_repo, round_num=42, phase=None, idempotency_s=5)

    assert second is not None
    assert second.sha == first.sha  # same ref returned, no new stash created


def test_stash_orphan_should_include_phase_in_message_when_phase_given(
    tmp_git_repo: Path,
) -> None:
    _make_dirty(tmp_git_repo)

    ref = stash_orphan(tmp_git_repo, round_num=7, phase="diverge")

    assert ref is not None
    assert "phase=diverge" in ref.message


def test_plugin_owned_paths_should_return_empty_list_when_no_registration() -> None:
    from agent_runner.vcs_state import plugin_owned_paths

    assert plugin_owned_paths() == []


def test_plugin_owned_paths_should_return_snapshot_when_paths_registered() -> None:
    from agent_runner.vcs_state import (
        plugin_owned_paths,
        register_plugin_owned_paths,
    )

    register_plugin_owned_paths(["proposals/", "reports/*.md"])

    assert plugin_owned_paths() == ["proposals/", "reports/*.md"]


def test_register_plugin_owned_paths_should_raise_value_error_when_entry_non_string() -> None:
    from agent_runner.vcs_state import register_plugin_owned_paths

    with pytest.raises(ValueError, match="non-string entry"):
        register_plugin_owned_paths(["ok.md", 42])  # type: ignore[list-item]


def test_matches_owned_path_should_match_as_prefix_when_pattern_has_trailing_slash() -> None:
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


def test_matches_owned_path_should_not_cross_slash_when_glob_pattern_has_no_slash() -> None:
    from agent_runner.vcs_state import (
        _matches_owned_path,
        register_plugin_owned_paths,
    )

    register_plugin_owned_paths(["reports/*.md"])

    assert _matches_owned_path("reports/dev.md")
    # PurePath.match: single * does NOT cross slashes
    assert not _matches_owned_path("reports/sub/qa.md")


def test_matches_owned_path_should_match_recursively_when_pattern_has_double_star() -> None:
    from agent_runner.vcs_state import (
        _matches_owned_path,
        register_plugin_owned_paths,
    )

    register_plugin_owned_paths(["logs/plugins/**/*"])

    assert _matches_owned_path("logs/plugins/acme/state.json")
    assert _matches_owned_path("logs/plugins/acme/deep/very/deep.txt")
    assert not _matches_owned_path("logs/other/state.json")


def test_matches_owned_path_should_match_when_any_pattern_matches() -> None:
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


def test_try_auto_commit_should_leave_git_head_unchanged_when_only_log_dir_churned(
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


def test_try_auto_commit_should_commit_excluding_log_dir_when_evolving_change_present(
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


def test_stash_orphan_should_exclude_log_dir_from_stash_when_log_dir_under_work_dir(
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


def test_stash_orphan_should_run_defense_when_log_dir_is_dash_prefixed_and_gitignored(
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


def test_stash_orphan_should_return_none_and_preserve_log_dir_when_only_log_dir_dirty(
    tmp_git_repo: Path,
) -> None:
    # A zero-work round that only churned log_dir: nothing to stash, logs survive.
    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    (log_dir / "events.jsonl").write_text("{}\n")

    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None, log_dir=log_dir)

    assert ref is None
    assert (log_dir / "events.jsonl").exists()


def test_try_auto_commit_should_return_sha_when_commit_succeeds(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "work.py").write_text("x = 1\n")

    sha = try_auto_commit(tmp_git_repo, 1, None)

    assert sha and len(sha) >= 7
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_git_repo, capture_output=True, text=True
    ).stdout.strip()
    assert head.startswith(sha) or sha == head


def test_try_auto_commit_should_return_empty_string_when_nothing_staged(
    tmp_git_repo: Path,
) -> None:
    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    (log_dir / "x.log").write_text("noise\n")  # only excluded bookkeeping

    assert try_auto_commit(tmp_git_repo, 1, None, log_dir=log_dir) == ""


def test_try_auto_commit_should_raise_auto_commit_error_when_not_git_repo(tmp_path: Path) -> None:
    with pytest.raises(AutoCommitError):
        try_auto_commit(tmp_path, 1, None)  # not a git repo
