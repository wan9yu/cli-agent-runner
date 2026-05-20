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


def test_given_window_zero_or_negative_when_parsed_then_argparse_rejects():
    """--window must be a positive int; 0 and negatives are rejected at parse time."""
    import pytest

    for bad in ("0", "-1", "-5"):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["peek", "--window", bad])


def test_peek_select_events_kind_is_removed(tmp_path, capsys):
    """0.1.34+: peek --select events.<kind> removed (use `events --kind <kind>`).
    Invocation should error like any other unknown selector.
    """
    from types import SimpleNamespace

    from agent_runner.cli import peek_cmd

    args = SimpleNamespace(
        select="events.agent_usage_recorded",
        window=10,
        json=False,
        round=None,
        log=False,
        events=None,
        config=None,
        work_dir=str(tmp_path),
    )
    # The selector is unknown — should fail, not succeed
    rc = peek_cmd.cmd_peek(args)
    assert rc != 0
    err = capsys.readouterr().err
    assert any(s in err.lower() for s in ("unknown", "invalid", "not found", "no such"))
