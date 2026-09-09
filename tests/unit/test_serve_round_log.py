"""Tests for round-log capture in serve_cmd."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import FakeArgs, make_toml


def _fake_spawn_ok(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs) -> int:
    """serve_cmd._spawn_round stand-in: clean exit, empty round log."""
    round_log_path.write_text("")
    return 0


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


def test_serve_cmd_should_create_round_log_file_when_round_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        round_log_path.write_text("round 1 output\n")
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    serve_cmd.cmd(FakeArgs(cfg_path))

    round_log = log_dir / "round-1.log"
    assert round_log.exists()
    assert "round 1 output" in round_log.read_text()


def test_serve_cmd_should_point_current_symlink_to_latest_round_log_when_round_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_ok)

    serve_cmd.cmd(FakeArgs(cfg_path))

    symlink = log_dir / "round-current.log"
    assert symlink.is_symlink()
    assert symlink.resolve() == (log_dir / "round-1.log").resolve()


def test_serve_cmd_should_continue_log_numbering_from_status_when_round_num_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If status.json has round_num=5, next round's log is round-6.log (counter sync)."""
    from agent_runner.cli import serve_cmd
    from agent_runner.context_store import Status, write_status

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    write_status(log_dir, Status(round_num=5, running=False))

    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_ok)

    serve_cmd.cmd(FakeArgs(cfg_path))

    assert (log_dir / "round-6.log").exists()
    assert not (log_dir / "round-1.log").exists()


def test_serve_cmd_should_prune_old_round_logs_when_retention_exceeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)

    # Create 6 old round logs with explicit mtimes — deterministic, no sleep needed
    _write_old_round_logs(log_dir, 6)
    cfg_path = _toml_with_retention(tmp_path, log_dir, retention=3)

    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_ok)

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


def test_serve_cmd_should_skip_pruning_when_retention_is_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd
    from tests._test_helpers import read_events_for_current_month

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    _write_old_round_logs(log_dir, 50)
    cfg_path = _toml_with_retention(tmp_path, log_dir, retention=0)

    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_ok)

    serve_cmd.cmd(FakeArgs(cfg_path))

    for i in range(1, 51):
        assert (log_dir / f"round-{i}.log").exists(), f"round-{i}.log was deleted"
    assert not [
        e
        for e in read_events_for_current_month(log_dir)
        if e["event"] == "round_logs_prune_deferred"
    ]


def test_serve_cmd_should_defer_prune_and_emit_event_when_backlog_exceeds_retention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lowering retention far below the existing backlog defers the whole prune:
    no file is deleted, one round_logs_prune_deferred is emitted, serve runs on.
    """
    from agent_runner.cli import serve_cmd
    from tests._test_helpers import read_events_for_current_month

    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    _write_old_round_logs(log_dir, 10)
    cfg_path = _toml_with_retention(tmp_path, log_dir, retention=2)

    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_ok)

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


def _minimal_cfg(work_dir: Path, log_dir: Path):
    """Smallest Config that satisfies _capture_substrate's cfg.runtime access."""
    from agent_runner.config import AgentConfig, Config, PromptConfig, RuntimeConfig

    return Config(
        agent=AgentConfig(command=["true"], prompt_arg_template=["{prompt}"]),
        runtime=RuntimeConfig(work_dir=work_dir, log_dir=log_dir, substrate_fingerprint_paths=[]),
        prompt=PromptConfig(file=work_dir / "prompt.md"),
    )


def test_capture_substrate_should_emit_matching_before_and_after_events(
    tmp_path: Path,
) -> None:
    from agent_runner.cli import serve_cmd
    from tests._test_helpers import read_events_for_current_month

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    cfg = _minimal_cfg(work_dir, log_dir)

    serve_cmd._capture_substrate(work_dir, cfg, log_dir, round_num=7, when="before")
    serve_cmd._capture_substrate(work_dir, cfg, log_dir, round_num=7, when="after")

    events = read_events_for_current_month(log_dir)
    before = [e for e in events if e["event"] == "round_substrate_before"]
    after = [e for e in events if e["event"] == "round_substrate_after"]
    assert len(before) == 1
    assert len(after) == 1
    assert before[0]["round_num"] == 7
    assert after[0]["round_num"] == 7
    # No git repo, no fingerprint paths configured: both fields null on both events.
    assert before[0]["git_head"] == after[0]["git_head"] is None
    assert before[0]["paths_hash"] == after[0]["paths_hash"] is None
