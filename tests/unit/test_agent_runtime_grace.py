"""Tests for max_grace_after_result_s HUNG defense (0.1.31+)."""

from __future__ import annotations

from pathlib import Path

from agent_runner.agent_runtime import run


def _write_fake_script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake.sh"
    p.write_text(f"#!/bin/bash\nset -e\n{body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_grace_kill_fires_when_result_then_silent(tmp_path):
    """Fake agent writes type=result then sleeps 5s. max_grace=1s -> kill within 3s."""
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result","is_error":false}\'\nsleep 5\n',
    )
    log_path = tmp_path / "round.log"
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=10,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
    )
    assert result.killed_for_grace is True
    assert result.duration_s < 4  # ~1s grace + tick latency + reap grace


def test_no_grace_kill_when_disabled(tmp_path):
    """max_grace=0 -> grace logic disabled; wall timeout governs."""
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result","is_error":false}\'\nsleep 5\n',
    )
    log_path = tmp_path / "round.log"
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=2,  # short wall timeout
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=0,
    )
    assert result.killed_for_grace is False
    assert result.timed_out is True  # killed by wall timeout instead


def test_no_grace_kill_when_result_not_emitted(tmp_path):
    """No result event -> grace countdown never starts."""
    script = _write_fake_script(tmp_path, 'echo "no result here"\nexit 0\n')
    log_path = tmp_path / "round.log"
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
    )
    assert result.killed_for_grace is False
    assert result.timed_out is False
    assert result.exit_code == 0
