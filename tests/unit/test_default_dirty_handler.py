"""Tests for DefaultDirtyHandler — bundled default dirty-tree handler."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_runner.builtin_plugins.default_dirty_handler import DefaultDirtyHandler
from agent_runner.hooks import VcsHookView
from agent_runner.vcs_state import StashError, stash_orphan
from tests._test_helpers import make_hook_context, read_events_for_current_month


def _ctx(tmp_git_repo, action):
    return make_hook_context(
        work_dir=tmp_git_repo,
        log_dir=tmp_git_repo / "logs",
        vcs=VcsHookView(dirty_action=action, stash_idempotency_s=5),
    )


def test_handle_dirty_should_return_stashed_when_action_is_stash(tmp_git_repo):
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")

    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "stash"), ["w.py"])

    assert out.kind == "stashed" and out.ref


def test_handle_dirty_should_return_ignored_when_action_is_ignore(tmp_git_repo):
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")

    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "ignore"), ["w.py"])

    assert out.kind == "ignored"


def test_handle_dirty_should_return_committed_when_action_is_auto_commit(tmp_git_repo):
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")

    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "auto_commit"), ["w.py"])

    assert out.kind == "committed" and out.ref


def test_handle_dirty_should_return_ignored_when_auto_commit_stages_nothing(
    tmp_git_repo, monkeypatch
):
    (tmp_git_repo / "logs").mkdir()
    import agent_runner.api as _api

    monkeypatch.setattr(_api, "try_auto_commit", lambda *a, **kw: "")

    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "auto_commit"), [])

    assert out.kind == "ignored"


def test_handle_dirty_should_emit_failure_and_leave_tree_dirty_when_auto_commit_raises(
    tmp_git_repo, monkeypatch
):
    """Parity with runner.py: AutoCommitError emits DIRTY_COMMIT_FAILED and
    leaves the tree dirty (no stash, no orphan_stashed)."""
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")
    import agent_runner.api as _api
    from agent_runner.vcs_state import AutoCommitError

    def _raise(*a, **kw):
        raise AutoCommitError("git fail")

    monkeypatch.setattr(_api, "try_auto_commit", _raise)

    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "auto_commit"), ["w.py"])

    assert out.kind == "ignored"
    kinds = [e.get("event") for e in read_events_for_current_month(tmp_git_repo / "logs")]
    assert "dirty_commit_failed" in kinds
    assert "orphan_stashed" not in kinds  # no stash fallback
    # Tree left dirty for the next round.
    assert (tmp_git_repo / "w.py").read_text() == "x=1\n"


def test_handle_dirty_should_write_orphan_state_file_when_action_is_stash(tmp_git_repo):
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")

    DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "stash"), ["w.py"])

    orphan_files = list((tmp_git_repo / "logs").glob("orphan*.json"))
    assert orphan_files, "orphan state file should be written to log_dir"


def _intent_to_add_dirty(repo: Path) -> None:
    """Dirty the tree the way a real agent does: untracked files made visible to
    ``git diff`` with ``git add -N``. This is what makes ``git stash push -u``
    hard-fail rc=1 with "Entry '<f>' not uptodate. Cannot merge."."""
    (repo / "base.txt").write_text("agent work\n")
    (repo / "new.txt").write_text("wip\n")
    subprocess.run(["git", "add", "-N", "."], cwd=repo, check=True)


def test_stash_orphan_should_raise_stash_error_when_intent_to_add_dirties_tree(
    tmp_git_repo: Path,
) -> None:
    """A failed push must not be reported as a clean tree — the WIP is still on disk."""
    _intent_to_add_dirty(tmp_git_repo)

    with pytest.raises(StashError) as exc:
        stash_orphan(tmp_git_repo, round_num=1, phase=None)

    assert "not uptodate" in str(exc.value)


def test_handle_dirty_should_emit_orphan_stash_failed_when_stash_push_fails(
    tmp_git_repo: Path,
) -> None:
    """The flagship defense failing silently is what made this kind dead."""
    (tmp_git_repo / "logs").mkdir()
    _intent_to_add_dirty(tmp_git_repo)

    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "stash"), ["base.txt"])

    assert out.kind == "ignored"
    failed = [
        e
        for e in read_events_for_current_month(tmp_git_repo / "logs")
        if e["event"] == "orphan_stash_failed"
    ]
    assert len(failed) == 1
    assert "not uptodate" in failed[0]["reason"]


def test_handle_dirty_should_emit_idempotent_skip_when_stashed_twice_in_same_round(
    tmp_git_repo: Path,
) -> None:
    """Reuse inside the idempotency window must not re-emit orphan_stashed."""
    (tmp_git_repo / "logs").mkdir()
    handler = DefaultDirtyHandler()
    (tmp_git_repo / "w.py").write_text("x=1\n")

    first = handler.handle_dirty(_ctx(tmp_git_repo, "stash"), ["w.py"])
    (tmp_git_repo / "w.py").write_text("x=2\n")
    second = handler.handle_dirty(_ctx(tmp_git_repo, "stash"), ["w.py"])

    assert first.ref == second.ref
    kinds = [e["event"] for e in read_events_for_current_month(tmp_git_repo / "logs")]
    assert kinds.count("orphan_stashed") == 1
    assert kinds.count("orphan_idempotent_skip") == 1


def test_stash_orphan_should_return_none_when_foreign_stash_and_churn_is_only_excluded_paths(
    tmp_git_repo: Path,
) -> None:
    """The ``msg not in raw_subj`` guard is reachable and load-bearing.

    With a pre-existing unrelated stash on top, a round that churns only excluded
    paths makes ``git stash push -u -- ':(exclude)logs'`` print "No local changes to
    save" and return rc=0 — so the push-failure branch does not catch it — and the
    ``-1`` listing then returns the older, foreign stash. Handing that SHA back would
    write a stranger's work into orphan-state.json under a fabricated ORPHAN message,
    and the operator would believe their WIP was safely stashed.
    """
    # A foreign stash — someone else's work — sits on top of the stack.
    (tmp_git_repo / "README.md").write_text("someone else's precious work\n")
    subprocess.run(
        ["git", "stash", "push", "-q", "-m", "FOREIGN unrelated work"],
        cwd=tmp_git_repo,
        check=True,
    )
    foreign = subprocess.run(
        ["git", "stash", "list", "-1", "--format=%H"],
        cwd=tmp_git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert foreign, "fixture precondition: a foreign stash must exist"

    # A round that churned only log_dir: everything dirty is excluded from the pathspec.
    log_dir = tmp_git_repo / "logs"
    log_dir.mkdir()
    (log_dir / "events.jsonl").write_text("{}\n")

    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None, log_dir=log_dir)

    assert ref is None, f"handed back foreign stash {foreign} as this round's orphan"
