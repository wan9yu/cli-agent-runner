"""Tests for api.read_round_num public helper."""

from __future__ import annotations

from pathlib import Path


def test_read_round_num_should_return_zero_when_no_status_file(tmp_path: Path) -> None:
    from agent_runner.api import read_round_num

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    assert read_round_num(log_dir) == 0


def test_read_round_num_should_return_round_num_when_status_file_present(
    tmp_path: Path,
) -> None:
    from agent_runner.api import read_round_num
    from agent_runner.context_store import Status, write_status

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_status(log_dir, Status(round_num=7, running=False))

    assert read_round_num(log_dir) == 7
