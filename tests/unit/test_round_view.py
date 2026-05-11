from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.round_view import build_round_view, resolve_round_arg


def test_given_int_arg_when_resolved_then_returns_same(tmp_path: Path) -> None:
    assert resolve_round_arg(42, tmp_path) == 42


def test_given_int_string_arg_when_resolved_then_returns_int(tmp_path: Path) -> None:
    assert resolve_round_arg("7", tmp_path) == 7


def test_given_none_when_resolved_then_returns_none(tmp_path: Path) -> None:
    assert resolve_round_arg(None, tmp_path) is None


def test_given_latest_when_no_rounds_then_returns_none(tmp_path: Path) -> None:
    assert resolve_round_arg("latest", tmp_path) is None


def test_given_latest_when_rounds_exist_then_returns_max(tmp_path: Path) -> None:
    (tmp_path / "rounds").mkdir()
    (tmp_path / "rounds" / "R1-2026.log").write_text("x")
    (tmp_path / "rounds" / "R5-2026.log").write_text("x")
    (tmp_path / "rounds" / "R3-2026.log").write_text("x")
    assert resolve_round_arg("latest", tmp_path) == 5


def test_given_garbage_arg_when_resolved_then_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="round"):
        resolve_round_arg("not-a-number", tmp_path)


def test_given_round_with_log_when_built_then_round_view_has_tail(tmp_path: Path) -> None:
    (tmp_path / "rounds").mkdir()
    (tmp_path / "rounds" / "R1-2026.log").write_text("line1\nline2\nline3\n")
    events = [
        {"ts": "2026-05-01T00:00:00Z", "event": "round_start", "round_num": 1},
        {"ts": "2026-05-01T00:00:01Z", "event": "agent_exit", "round_num": 1,
         "exit_code": 0, "duration_s": 42.0, "timed_out": False},
    ]
    rv = build_round_view(tmp_path, 1, events, want_log=True)
    assert rv is not None
    assert rv.round_num == 1
    assert rv.exit_code == 0
    assert rv.duration_so_far_s == 42.0
    assert rv.log_tail and "line3" in rv.log_tail


def test_given_round_without_log_when_built_then_no_tail(tmp_path: Path) -> None:
    (tmp_path / "rounds").mkdir()
    (tmp_path / "rounds" / "R1-2026.log").write_text("x")
    rv = build_round_view(tmp_path, 1, [], want_log=False)
    assert rv is not None
    assert rv.log_tail is None


def test_given_missing_round_when_built_then_returns_none(tmp_path: Path) -> None:
    (tmp_path / "rounds").mkdir()
    rv = build_round_view(tmp_path, 99, [], want_log=False)
    assert rv is None
