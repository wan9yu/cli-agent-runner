"""Tests for round-log capture in serve_cmd."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import FakeArgs, make_toml


def _write_old_round_logs(log_dir: Path, count: int) -> None:
    """Create ``count`` round-<N>.log files with explicit ascending mtimes."""
    import os

    for i in range(1, count + 1):
        path = log_dir / f"round-{i}.log"
        path.write_text(f"old round {i}")
        os.utime(path, (1000000.0 + i, 1000000.0 + i))


def _toml_with_retention(tmp_path: Path, log_dir: Path, *, retention: int) -> Path:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")
    cfg_path = tmp_path / "agent-runner.toml"
    cfg_path.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        f"round_log_retention = {retention}\n"
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )
    return cfg_path


def test_given_round_runs_when_serve_then_round_log_file_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round subprocess output goes to log_dir/round-<N>.log."""
    import subprocess

    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    def fake_run(*_a, **kwargs):
        stdout = kwargs.get("stdout")
        if stdout:
            stdout.write("round 1 output\n")
            stdout.flush()
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)

    serve_cmd.cmd(FakeArgs(cfg_path))

    round_log = log_dir / "round-1.log"
    assert round_log.exists()
    assert "round 1 output" in round_log.read_text()


def test_given_round_runs_when_serve_then_current_symlink_points_to_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """round-current.log symlink points to the latest round-<N>.log."""
    import subprocess

    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": 0})())

    serve_cmd.cmd(FakeArgs(cfg_path))

    symlink = log_dir / "round-current.log"
    assert symlink.is_symlink()
    assert symlink.resolve() == (log_dir / "round-1.log").resolve()


def test_given_existing_round_num_when_serve_then_log_filename_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If status.json has round_num=5, next round's log is round-6.log (counter sync)."""
    import subprocess

    from agent_runner.cli import serve_cmd
    from agent_runner.context_store import Status, write_status

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    write_status(log_dir, Status(round_num=5, running=False))

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": 0})())

    serve_cmd.cmd(FakeArgs(cfg_path))

    assert (log_dir / "round-6.log").exists()
    assert not (log_dir / "round-1.log").exists()


def test_given_retention_exceeded_when_serve_starts_then_old_logs_pruned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Old round-<N>.log files beyond round_log_retention pruned at serve start."""
    import subprocess

    from agent_runner.cli import serve_cmd

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)

    # Create 6 old round logs with explicit mtimes — deterministic, no sleep needed
    _write_old_round_logs(log_dir, 6)
    cfg_path = _toml_with_retention(tmp_path, log_dir, retention=3)

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": 0})())

    serve_cmd.cmd(FakeArgs(cfg_path))

    # After serve startup pruning: retention=3 keeps the 3 most-recent old files
    # (rounds 4, 5, 6); rounds 1, 2, 3 are pruned. Deleting 3 to keep 3 is not a
    # bulk prune, so the guard does not trip.
    assert not (log_dir / "round-1.log").exists()
    assert not (log_dir / "round-2.log").exists()
    assert not (log_dir / "round-3.log").exists()
    assert (log_dir / "round-4.log").exists()
    assert (log_dir / "round-5.log").exists()
    assert (log_dir / "round-6.log").exists()


def test_given_retention_zero_when_serve_starts_then_no_prune_and_no_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default (0) leaves the serve-level family alone and stays silent."""
    import subprocess

    from agent_runner.cli import serve_cmd
    from tests._test_helpers import read_events_for_current_month

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    _write_old_round_logs(log_dir, 50)
    cfg_path = _toml_with_retention(tmp_path, log_dir, retention=0)

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": 0})())

    serve_cmd.cmd(FakeArgs(cfg_path))

    for i in range(1, 51):
        assert (log_dir / f"round-{i}.log").exists(), f"round-{i}.log was deleted"
    assert not [
        e
        for e in read_events_for_current_month(log_dir)
        if e["event"] == "round_logs_prune_deferred"
    ]


def test_given_bulk_backlog_when_serve_starts_then_prune_deferred_and_emitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lowering retention far below the existing backlog defers the whole prune:
    no file is deleted, one round_logs_prune_deferred is emitted, serve runs on.
    """
    import subprocess

    from agent_runner.cli import serve_cmd
    from tests._test_helpers import read_events_for_current_month

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    _write_old_round_logs(log_dir, 10)
    cfg_path = _toml_with_retention(tmp_path, log_dir, retention=2)

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": 0})())

    serve_cmd.cmd(FakeArgs(cfg_path))

    for i in range(1, 11):
        assert (log_dir / f"round-{i}.log").exists(), f"round-{i}.log was deleted"
    # The round still ran.
    assert (log_dir / "round-11.log").exists()

    deferrals = [
        e
        for e in read_events_for_current_month(log_dir)
        if e["event"] == "round_logs_prune_deferred"
    ]
    assert len(deferrals) == 1
    assert deferrals[0]["directory"] == str(log_dir)
    assert deferrals[0]["existing"] == 10
    assert deferrals[0]["keep"] == 2
    assert deferrals[0]["would_delete"] == 8
