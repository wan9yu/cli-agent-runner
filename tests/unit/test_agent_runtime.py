from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent_runner.agent_runtime import RunResult, run
from tests._test_helpers import poll_until


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
        work_dir=tmp_path,
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
        work_dir=tmp_path,
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
        work_dir=tmp_path,
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
        work_dir=tmp_path,
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
        work_dir=tmp_path,
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
        work_dir=tmp_path,
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
    """Spawn a subprocess that itself spawns a child; verify both die on timeout.

    d3ece37 lesson: the pre-fix version used a hard-coded ``/tmp`` pidfile path
    plus a sole assert guarded by ``if pid_file.exists()`` — under load, a
    pidfile that hadn't appeared yet (or a stale one from a previous run) made
    this PASS VACUOUSLY, verifying nothing. ``tmp_path`` isolates the pidfile
    per test run; ``poll_until`` fails LOUDLY (not silently) when the
    grandchild is never observed or never reaped.
    """
    pid_file = tmp_path / "child.pid"
    script = _bash_script(
        tmp_path,
        f"sleep 30 & echo $! > {pid_file} ; wait",
    )
    run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=2,
        log_path=tmp_path / "out.log",
        env_extra={},
    )
    assert poll_until(pid_file.exists, timeout_s=5), (
        f"grandchild never wrote its pidfile at {pid_file} — script did not run"
    )
    child_pid = int(pid_file.read_text().strip())

    def _child_is_dead() -> bool:
        try:
            os.kill(child_pid, 0)  # signal 0: no-op, raises once the pid is gone
        except ProcessLookupError:
            return True
        return False

    assert poll_until(_child_is_dead, timeout_s=5), (
        f"grandchild pid {child_pid} survived the kill — orphaned, not reaped"
    )


def test_given_empty_env_extra_when_run_then_no_implicit_env_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.1.7: agent_runtime injects nothing — caller's env_extra is verbatim."""
    monkeypatch.delenv("DISABLE_AUTOUPDATER", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EFFORT_LEVEL", raising=False)
    script = _bash_script(
        tmp_path,
        'echo "AUTOUPDATER=${DISABLE_AUTOUPDATER:-unset}"; '
        'echo "EFFORT=${CLAUDE_CODE_EFFORT_LEVEL:-unset}"; exit 0',
    )
    log = tmp_path / "out.log"
    run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        log_path=log,
        env_extra={},
    )
    text = log.read_text()
    assert "AUTOUPDATER=unset" in text
    assert "EFFORT=unset" in text


def test_given_work_dir_when_run_then_child_executes_in_work_dir(tmp_path: Path) -> None:
    """The agent child runs in work_dir, not the supervisor's cwd."""
    work = tmp_path / "the-work-dir"
    work.mkdir()
    script = _bash_script(tmp_path, "pwd -P")
    log = tmp_path / "out.log"
    result = run(
        work_dir=work,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="ignored",
        timeout_s=10,
        log_path=log,
        env_extra={},
    )
    assert result.exit_code == 0
    assert log.read_text().strip() == str(work.resolve())


def test_given_stderr_output_when_run_then_merged_into_round_log(tmp_path: Path) -> None:
    """stderr=STDOUT is load-bearing: oauth_fail/network_fail regex-scan stderr
    text out of the round log (contract: hooks.HookContext.agent_log_path)."""
    script = _bash_script(tmp_path, "echo OUT_LINE; echo ERR_MARKER >&2")
    log = tmp_path / "out.log"
    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="ignored",
        timeout_s=10,
        log_path=log,
        env_extra={},
    )
    assert result.exit_code == 0
    text = log.read_text()
    assert "OUT_LINE" in text
    assert "ERR_MARKER" in text
