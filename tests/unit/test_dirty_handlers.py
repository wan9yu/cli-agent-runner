"""Tests for DirtyHandler seam: registry, dispatch, and failure-isolation."""

from __future__ import annotations

from agent_runner import hooks
from agent_runner.api_types import DirtyOutcome
from tests._test_helpers import make_hook_context


def _mk(name, prio, out):
    class H:
        pass

    h = H()
    h.name = name
    h.priority = prio
    h.handle_dirty = lambda ctx, files: out
    return h


def test_first_non_none_by_priority_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "_DIRTY_HANDLERS", [])
    hooks.register_dirty_handler(_mk("late", 1000, DirtyOutcome("stashed", "S")))
    hooks.register_dirty_handler(_mk("early", 0, DirtyOutcome("committed", "C")))
    ctx = make_hook_context(work_dir=tmp_path, log_dir=tmp_path)
    out = hooks.dispatch_dirty(ctx, ["a.md"], log_dir=tmp_path)
    assert out == DirtyOutcome("committed", "C")  # priority 0 ran first


def test_pass_falls_through_to_next(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "_DIRTY_HANDLERS", [])
    hooks.register_dirty_handler(_mk("passer", 0, None))
    hooks.register_dirty_handler(_mk("default", 1000, DirtyOutcome("stashed", "S")))
    ctx = make_hook_context(work_dir=tmp_path, log_dir=tmp_path)
    assert hooks.dispatch_dirty(ctx, ["a"], tmp_path).kind == "stashed"


def test_raising_handler_is_isolated_and_treated_as_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "_DIRTY_HANDLERS", [])

    def boom(ctx, files):
        raise RuntimeError("x")

    bad = _mk("bad", 0, None)
    bad.handle_dirty = boom
    hooks.register_dirty_handler(bad)
    hooks.register_dirty_handler(_mk("default", 1000, DirtyOutcome("ignored")))
    ctx = make_hook_context(work_dir=tmp_path, log_dir=tmp_path)
    assert hooks.dispatch_dirty(ctx, ["a"], tmp_path).kind == "ignored"
