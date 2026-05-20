"""Tests for peek --select (dot-path subtree selector)."""

from __future__ import annotations

from agent_runner.cli import _build_parser


def test_given_select_malformed_events_prefix_when_parsed_then_argparse_exits(capsys):
    """--select foo.bar (not events.<kind> form) is passed to existing subtree logic,
    not the events selector — this test verifies the argparse arg itself is accepted
    (validation happens at dispatch time, not parse time)."""
    args = _build_parser().parse_args(["peek", "--select", "foo.bar"])
    # Argparse accepts it; dispatch determines fate at runtime
    assert args.select == "foo.bar"


def test_peek_select_events_kind_is_removed():
    """0.1.34+: peek --select events.<kind> no longer special-cased.

    Exercises select_path directly against a sample ProjectState-like tree so
    we prove the selector hits generic dot-path traversal (which has no
    ``events`` attribute) instead of the deleted ``_run_events_select`` helper.
    """
    import pytest

    from agent_runner.api_types import select_path

    # Minimal tree mirroring ProjectState shape; no 'events' attribute.
    tree = {"system": {"disk_used_pct": 50.0}, "plugins": {"event_kinds": []}}
    with pytest.raises(KeyError, match="events"):
        select_path(tree, "events.agent_usage_recorded")


def test_peek_cmd_has_no_window_flag():
    """0.1.34+: --window was only consumed by the removed events.* selector.
    Verify argparse rejects --window on peek so it stays removed.
    """
    import pytest

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["peek", "--window", "10"])
