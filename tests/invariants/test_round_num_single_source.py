"""Invariant: serve and the round child derive round_num through ONE function.

Skew regression (0.2.12): serve computed next_round_num() while the child did
prev_status.round_num + 1 — a child that crashed before writing status.json let
the two disagree and re-use a number whose serve-log already existed.
"""

from __future__ import annotations

from pathlib import Path

from agent_runner import runner
from agent_runner.context_store import Status, write_status
from agent_runner.round_log import next_round_num


def test_child_without_env_matches_serve_derivation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_RUNNER_ROUND_NUM", raising=False)
    assert runner._resolve_round_num(tmp_path) == next_round_num(tmp_path) == 1
    write_status(tmp_path, Status(round_num=5, running=False))
    (tmp_path / "round-7.log").write_text("stray", encoding="utf-8")
    assert runner._resolve_round_num(tmp_path) == next_round_num(tmp_path) == 8


def test_child_honors_serve_supplied_env(tmp_path: Path, monkeypatch) -> None:
    write_status(tmp_path, Status(round_num=5, running=False))
    monkeypatch.setenv("AGENT_RUNNER_ROUND_NUM", "42")
    assert runner._resolve_round_num(tmp_path) == 42


def test_crashed_child_does_not_reuse_existing_log_number(tmp_path: Path, monkeypatch) -> None:
    # status stuck at 4 (child crashed before write) but its R5 serve-log exists.
    write_status(tmp_path, Status(round_num=4, running=False))
    (tmp_path / "round-5.log").write_text("crashed", encoding="utf-8")
    serve_num = next_round_num(tmp_path)
    assert serve_num == 6  # max(status 4, file 5) + 1 — never 5
    monkeypatch.setenv("AGENT_RUNNER_ROUND_NUM", str(serve_num))
    assert runner._resolve_round_num(tmp_path) == 6


def test_garbage_env_falls_back_to_file_counter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_RUNNER_ROUND_NUM", "not-an-int")
    assert runner._resolve_round_num(tmp_path) == next_round_num(tmp_path) == 1
