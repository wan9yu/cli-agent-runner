"""Tests for api.read_round_num public helper."""

from __future__ import annotations

from pathlib import Path


def test_given_no_status_file_when_read_round_num_then_returns_zero(tmp_path: Path) -> None:
    """No status.json present → returns 0 (fresh project)."""
    from agent_runner.api import read_round_num

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    assert read_round_num(log_dir) == 0


def test_given_status_file_with_round_num_when_read_round_num_then_returns_it(
    tmp_path: Path,
) -> None:
    """status.json with round_num=7 → read_round_num returns 7."""
    from agent_runner.api import read_round_num
    from agent_runner.context_store import Status, write_status

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_status(log_dir, Status(round_num=7, running=False))
    assert read_round_num(log_dir) == 7
