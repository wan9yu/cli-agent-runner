"""Tests for DefaultDirtyHandler — bundled default dirty-tree handler."""

from __future__ import annotations

from agent_runner.builtin_plugins.default_dirty_handler import DefaultDirtyHandler
from agent_runner.hooks import VcsHookView
from tests._test_helpers import make_hook_context, read_events_for_current_month


def _ctx(tmp_git_repo, action):
    return make_hook_context(
        work_dir=tmp_git_repo,
        log_dir=tmp_git_repo / "logs",
        vcs=VcsHookView(dirty_action=action, stash_idempotency_s=5),
    )


def test_stash_action_returns_stashed(tmp_git_repo):
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")
    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "stash"), ["w.py"])
    assert out.kind == "stashed" and out.ref


def test_ignore_action_returns_ignored(tmp_git_repo):
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")
    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "ignore"), ["w.py"])
    assert out.kind == "ignored"


def test_auto_commit_action_returns_committed(tmp_git_repo):
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")
    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "auto_commit"), ["w.py"])
    assert out.kind == "committed" and out.ref


def test_auto_commit_nothing_staged_returns_ignored(tmp_git_repo, monkeypatch):
    """When try_auto_commit returns '' (nothing staged), outcome is ignored."""
    (tmp_git_repo / "logs").mkdir()
    import agent_runner.api as _api

    monkeypatch.setattr(_api, "try_auto_commit", lambda *a, **kw: "")
    out = DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "auto_commit"), [])
    assert out.kind == "ignored"


def test_auto_commit_error_emits_failure_and_leaves_tree_dirty(tmp_git_repo, monkeypatch):
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


def test_stash_writes_orphan_state(tmp_git_repo):
    """Stash action writes orphan state file to log_dir."""
    (tmp_git_repo / "logs").mkdir()
    (tmp_git_repo / "w.py").write_text("x=1\n")
    DefaultDirtyHandler().handle_dirty(_ctx(tmp_git_repo, "stash"), ["w.py"])
    orphan_files = list((tmp_git_repo / "logs").glob("orphan*.json"))
    assert orphan_files, "orphan state file should be written to log_dir"
