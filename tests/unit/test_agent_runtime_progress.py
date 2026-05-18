"""Tests for round_progress heartbeat (0.1.32+)."""

from __future__ import annotations

from pathlib import Path

from agent_runner.agent_runtime import run


def _write_fake_script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake.sh"
    p.write_text(f"#!/bin/bash\nset -e\n{body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_given_progress_callback_with_interval_when_run_then_called_at_least_twice(tmp_path):
    """interval=1s, script sleeps 3s -> callback called >=2 times."""
    script = _write_fake_script(tmp_path, "sleep 3")
    log_path = tmp_path / "round.log"
    calls: list[dict] = []
    run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=10,
        log_path=log_path,
        env_extra={},
        progress_callback=calls.append,
        progress_interval_s=1,
    )
    assert len(calls) >= 2
    assert all("wall_age_s" in c for c in calls)
    assert all("log_size_kb" in c for c in calls)
    assert all("last_write_age_s" in c for c in calls)


def test_given_progress_interval_zero_when_run_then_callback_never_called(tmp_path):
    """interval=0 -> callback never called regardless of duration."""
    script = _write_fake_script(tmp_path, "sleep 1")
    log_path = tmp_path / "round.log"
    calls: list[dict] = []
    run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        log_path=log_path,
        env_extra={},
        progress_callback=calls.append,
        progress_interval_s=0,
    )
    assert calls == []


def test_given_progress_callback_none_when_run_then_no_crash(tmp_path):
    """progress_callback=None with non-zero interval -> no crash."""
    script = _write_fake_script(tmp_path, 'echo done\nexit 0')
    log_path = tmp_path / "round.log"
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        log_path=log_path,
        env_extra={},
        progress_callback=None,
        progress_interval_s=1,
    )
    assert result.exit_code == 0
