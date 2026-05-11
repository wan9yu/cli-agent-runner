from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent_runner.agent_runtime import (
    CRITICAL_ENV_DEFAULTS,
    RunResult,
    install_sigterm_reaper,
    merge_critical_envs,
    run,
)


def _bash_script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake.sh"
    p.write_text(f"#!/usr/bin/env bash\n{body}\n")
    p.chmod(0o755)
    return p


def test_given_subprocess_within_timeout_when_run_then_returns_exit_code_zero(
    tmp_path: Path,
) -> None:
    script = _bash_script(tmp_path, "echo hello; exit 0")
    log = tmp_path / "out.log"
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="ignored",
        timeout_s=10,
        log_path=log,
        env_extra={},
    )
    assert isinstance(result, RunResult)
    assert result.exit_code == 0
    assert result.duration_s < 10
    assert "hello" in log.read_text()


def test_given_subprocess_returning_nonzero_when_run_then_exit_code_propagated(
    tmp_path: Path,
) -> None:
    script = _bash_script(tmp_path, "exit 7")
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=10,
        log_path=tmp_path / "out.log",
        env_extra={},
    )
    assert result.exit_code == 7


def test_given_subprocess_exceeds_timeout_when_run_then_kills_process_group(
    tmp_path: Path,
) -> None:
    script = _bash_script(tmp_path, "sleep 30")
    start = time.time()
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=2,
        log_path=tmp_path / "out.log",
        env_extra={},
    )
    elapsed = time.time() - start
    assert result.timed_out is True
    assert result.exit_code != 0
    assert elapsed < 10  # killed quickly, not waited 30


def test_given_subprocess_emits_constant_activity_when_timeout_exceeded_then_killed_anyway(
    tmp_path: Path,
) -> None:
    """R1128 lesson — ROUND_TIMEOUT is wall-clock hard wall, not activity-based."""
    script = _bash_script(
        tmp_path,
        "while true; do echo activity; sleep 0.1; done",
    )
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=2,
        log_path=tmp_path / "out.log",
        env_extra={},
    )
    assert result.timed_out is True


def test_given_prompt_arg_template_when_run_then_prompt_substituted_in_argv(
    tmp_path: Path,
) -> None:
    script = _bash_script(tmp_path, 'echo "prompt-was=$2"; exit 0')
    log = tmp_path / "out.log"
    run(
        command=[str(script)],
        prompt_arg_template=["-p", "{prompt}"],
        prompt="HELLO",
        timeout_s=5,
        log_path=log,
        env_extra={},
    )
    assert "prompt-was=HELLO" in log.read_text()


def test_given_env_extra_when_run_then_envs_propagated_to_subprocess(tmp_path: Path) -> None:
    script = _bash_script(tmp_path, 'echo "EFFORT=$CLAUDE_CODE_EFFORT_LEVEL"; exit 0')
    log = tmp_path / "out.log"
    run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        log_path=log,
        env_extra={"CLAUDE_CODE_EFFORT_LEVEL": "xhigh"},
    )
    assert "EFFORT=xhigh" in log.read_text()


def test_given_subprocess_in_process_group_when_killed_then_descendants_terminate(
    tmp_path: Path,
) -> None:
    """Spawn a subprocess that itself spawns a child; verify both die on timeout."""
    script = _bash_script(
        tmp_path,
        "sleep 30 & echo $! > /tmp/agent_runner_test_child_pid; wait",
    )
    pid_file = Path("/tmp/agent_runner_test_child_pid")
    pid_file.unlink(missing_ok=True)
    run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=2,
        log_path=tmp_path / "out.log",
        env_extra={},
    )
    time.sleep(0.5)  # let kill propagate
    if pid_file.exists():
        child_pid = int(pid_file.read_text().strip())
        # Verify child died — sending signal 0 raises OSError if pid gone
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        pid_file.unlink(missing_ok=True)


def test_given_critical_env_defaults_when_inspected_then_contains_autoupdater_and_effort() -> None:
    assert CRITICAL_ENV_DEFAULTS["DISABLE_AUTOUPDATER"] == "1"
    assert CRITICAL_ENV_DEFAULTS["CLAUDE_CODE_EFFORT_LEVEL"] == "xhigh"


def test_given_user_env_when_merged_with_critical_then_critical_wins() -> None:
    merged = merge_critical_envs({"DISABLE_AUTOUPDATER": "0", "FOO": "bar"})
    assert merged["DISABLE_AUTOUPDATER"] == "1"  # critical override
    assert merged["FOO"] == "bar"  # user env preserved
    assert merged["CLAUDE_CODE_EFFORT_LEVEL"] == "xhigh"


def test_given_install_sigterm_reaper_when_called_then_returns_previous_handler() -> None:
    import signal as _sig

    prev = install_sigterm_reaper(lambda: None)
    assert prev is not None
    _sig.signal(_sig.SIGTERM, prev)  # restore
