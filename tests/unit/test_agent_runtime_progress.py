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
    """interval=1s, script sleeps 6s -> callback called >=2 times.

    Widened (0.2.19) from sleep 3/timeout_s=10: under >=2 concurrent gates,
    scheduling jitter on the progress-tick loop can eat into a short child's
    lifetime, leaving too little real time for 2 ticks to land before the
    child exits on its own. A 6s child gives the ticker several more real
    chances to fire before exit, well beyond just tightening the assertion.
    """
    script = _write_fake_script(tmp_path, "sleep 6")
    log_path = tmp_path / "round.log"
    calls: list[dict] = []
    run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=30,
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
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=30,  # widened from 15 (0.2.19): >=2 concurrent-gate headroom
        log_path=log_path,
        env_extra={},
        progress_callback=calls.append,
        progress_interval_s=0,
    )
    assert calls == []


def test_given_progress_callback_none_when_run_then_no_crash(tmp_path):
    """progress_callback=None with non-zero interval -> no crash.

    timeout_s=40 (widened from 15, 0.2.19): reproduced under >=2 concurrent
    gates -- see
    test_given_prompt_arg_template_when_run_then_prompt_substituted_in_argv
    in test_agent_runtime.py for the same trivial-echo starvation mechanism.
    """
    script = _write_fake_script(tmp_path, "echo done\nexit 0")
    log_path = tmp_path / "round.log"
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=40,
        log_path=log_path,
        env_extra={},
        progress_callback=None,
        progress_interval_s=1,
    )
    assert result.exit_code == 0
