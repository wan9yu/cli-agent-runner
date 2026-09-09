from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.round_view import build_round_view, resolve_round_arg


def test_int_arg_should_return_same_when_resolved(tmp_path: Path) -> None:
    assert resolve_round_arg(42, tmp_path) == 42


def test_int_string_arg_should_return_int_when_resolved(tmp_path: Path) -> None:
    assert resolve_round_arg("7", tmp_path) == 7


def test_none_arg_should_return_none_when_resolved(tmp_path: Path) -> None:
    assert resolve_round_arg(None, tmp_path) is None


def test_latest_should_return_none_when_no_rounds_exist(tmp_path: Path) -> None:
    assert resolve_round_arg("latest", tmp_path) is None


def test_latest_should_return_max_when_rounds_exist(tmp_path: Path) -> None:
    (tmp_path / "rounds").mkdir()
    (tmp_path / "rounds" / "R1-2026.log").write_text("x")
    (tmp_path / "rounds" / "R5-2026.log").write_text("x")
    (tmp_path / "rounds" / "R3-2026.log").write_text("x")

    assert resolve_round_arg("latest", tmp_path) == 5


def test_garbage_arg_should_raise_when_resolved(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="round"):
        resolve_round_arg("not-a-number", tmp_path)


def test_round_with_log_should_include_log_tail_when_built(tmp_path: Path) -> None:
    (tmp_path / "rounds").mkdir()
    (tmp_path / "rounds" / "R1-2026.log").write_text("line1\nline2\nline3\n")
    events = [
        {"ts": "2026-05-01T00:00:00Z", "event": "round_start", "round_num": 1},
        {
            "ts": "2026-05-01T00:00:01Z",
            "event": "agent_exit",
            "round_num": 1,
            "exit_code": 0,
            "duration_s": 42.0,
            "timed_out": False,
        },
    ]

    rv = build_round_view(tmp_path, 1, events, want_log=True)

    assert rv is not None
    assert rv.round_num == 1
    assert rv.exit_code == 0
    assert rv.duration_so_far_s == 42.0
    assert rv.log_tail and "line3" in rv.log_tail


def test_round_without_log_should_omit_tail_when_built(tmp_path: Path) -> None:
    (tmp_path / "rounds").mkdir()
    (tmp_path / "rounds" / "R1-2026.log").write_text("x")

    rv = build_round_view(tmp_path, 1, [], want_log=False)

    assert rv is not None
    assert rv.log_tail is None


def test_missing_round_should_return_none_when_built(tmp_path: Path) -> None:
    (tmp_path / "rounds").mkdir()

    rv = build_round_view(tmp_path, 99, [], want_log=False)

    assert rv is None
