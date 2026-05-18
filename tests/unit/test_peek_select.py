"""Tests for peek --select events.<kind> --window N selector (0.1.32+)."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runner.cli import _build_parser
from agent_runner.cli.peek_cmd import _run_events_select


def _write_events_file(log_dir: Path, filename: str, lines: list[dict]) -> None:
    f = log_dir / filename
    f.write_text(
        "\n".join(json.dumps(ln) for ln in lines) + "\n",
        encoding="utf-8",
    )


def test_given_events_select_when_three_matching_then_returns_last_two(tmp_path, capsys):
    """--select events.agent_usage_recorded --window 2 returns last 2 matching events."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_events_file(
        log_dir,
        "events-2026-05.jsonl",
        [
            {"event": "round_start", "round_num": 1},
            {"event": "agent_usage_recorded", "round_num": 1, "cost_usd": 0.01},
            {"event": "agent_usage_recorded", "round_num": 2, "cost_usd": 0.02},
            {"event": "agent_usage_recorded", "round_num": 3, "cost_usd": 0.03},
            {"event": "round_end", "round_num": 3},
        ],
    )

    result = _run_events_select(log_dir, kind="agent_usage_recorded", window=2, month_tag="2026-05")
    assert len(result) == 2
    assert result[0]["round_num"] == 2
    assert result[1]["round_num"] == 3


def test_given_events_select_when_kind_absent_then_returns_empty(tmp_path):
    """--select events.<kind> matching nothing returns []."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_events_file(
        log_dir,
        "events-2026-05.jsonl",
        [
            {"event": "round_start", "round_num": 1},
            {"event": "round_end", "round_num": 1},
        ],
    )

    result = _run_events_select(
        log_dir, kind="agent_usage_recorded", window=10, month_tag="2026-05"
    )
    assert result == []


def test_given_select_malformed_events_prefix_when_parsed_then_argparse_exits(capsys):
    """--select foo.bar (not events.<kind> form) is passed to existing subtree logic,
    not the events selector — this test verifies the argparse arg itself is accepted
    (validation happens at dispatch time, not parse time)."""
    args = _build_parser().parse_args(["peek", "--select", "foo.bar"])
    # Argparse accepts it; dispatch determines fate at runtime
    assert args.select == "foo.bar"
