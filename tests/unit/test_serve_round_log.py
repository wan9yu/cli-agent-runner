"""Tests for round-log capture in serve_cmd."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import make_toml


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

    class FakeArgs:
        config = cfg_path
        once = True

    serve_cmd.cmd(FakeArgs())

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

    class FakeArgs:
        config = cfg_path
        once = True

    serve_cmd.cmd(FakeArgs())

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

    class FakeArgs:
        config = cfg_path
        once = True

    serve_cmd.cmd(FakeArgs())

    assert (log_dir / "round-6.log").exists()
    assert not (log_dir / "round-1.log").exists()


def test_given_retention_exceeded_when_serve_starts_then_old_logs_pruned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Old round-<N>.log files beyond round_log_retention pruned at serve start."""
    import subprocess
    import time as _time

    from agent_runner.cli import serve_cmd

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)

    # Create 5 old round logs with increasing mtimes
    for i in range(1, 6):
        path = log_dir / f"round-{i}.log"
        path.write_text(f"old round {i}")
        # Stagger mtimes so the sort is deterministic
        _time.sleep(0.01)

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
        "round_log_retention = 2\n"
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: type("R", (), {"returncode": 0})())

    class FakeArgs:
        config = cfg_path
        once = True

    serve_cmd.cmd(FakeArgs())

    # After serve startup pruning: retention=2 means 2 most-recent old files kept (rounds 4, 5)
    # rounds 1, 2, 3 should be pruned
    assert not (log_dir / "round-1.log").exists()
    assert not (log_dir / "round-2.log").exists()
    assert not (log_dir / "round-3.log").exists()
    # rounds 4, 5 kept (most recent by mtime)
    assert (log_dir / "round-4.log").exists()
    assert (log_dir / "round-5.log").exists()
