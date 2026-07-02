"""Tests for event-kind catalogs and invariants."""

from __future__ import annotations


def test_dirty_auto_committed_is_builtin_kind() -> None:
    from agent_runner import events

    assert events.DIRTY_AUTO_COMMITTED == "dirty_auto_committed"
    assert "dirty_auto_committed" in events._BUILTIN_KINDS
