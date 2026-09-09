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


def test_subprocess_should_return_exit_code_zero_when_within_timeout(
    tmp_path: Path,
) -> None:
    script = _bash_script(tmp_path, "echo hello; exit 0")
    log = tmp_path / "out.log"

    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="ignored",
        # see test_prompt_arg_template_should_substitute_prompt_in_argv: contention headroom
        timeout_s=40,
        log_path=log,
        env_extra={},
    )

    assert isinstance(result, RunResult)
    assert result.exit_code == 0
    # Headroom over the configured timeout_s=40 -- the original bound was
    # exactly equal to it, leaving zero slack for scheduling jitter under a
    # busy parallel run (an "echo hello" that legitimately took right up to
    # timeout_s under contention would fail this assert even though it never
    # got killed -- exit_code == 0 above is what actually proves no escalation).
    assert result.duration_s < 45
    assert "hello" in log.read_text()


def test_subprocess_should_propagate_exit_code_when_nonzero(
    tmp_path: Path,
) -> None:
    script = _bash_script(tmp_path, "exit 7")

    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        # see test_prompt_arg_template_should_substitute_prompt_in_argv: contention headroom
        timeout_s=40,
        log_path=tmp_path / "out.log",
        env_extra={},
    )

    assert result.exit_code == 7


def test_subprocess_should_kill_process_group_when_timeout_exceeded(
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
    # 2x headroom for contention (timeout_s=2 -> ~2-3s expected); strictly
    # below the 30s un-escalated sleep so this still proves the kill fired.
    assert elapsed < 20  # killed quickly, not waited 30


def test_subprocess_emitting_constant_activity_should_be_killed_when_timeout_exceeded(
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


def test_prompt_arg_template_should_substitute_prompt_in_argv(
    tmp_path: Path,
) -> None:
    script = _bash_script(tmp_path, 'echo "prompt-was=$2"; exit 0')
    log = tmp_path / "out.log"
    # timeout_s=40 (widened from 15, then from 5): a trivial "echo; exit 0" is
    # expected to finish in milliseconds, but under `-n auto` contention on a
    # busy host, fork/exec scheduling was occasionally delayed several real
    # seconds -- a too-tight timeout_s can kill the pgroup before the echo
    # ever runs, leaving the log empty for a reason unrelated to the
    # argv-substitution property tested. Reproduced under >=2 CONCURRENT
    # gates (this test's own gate plus another full gate running at once,
    # ~2-3x CPU oversubscription): 15s was hit and the child was SIGTERMed
    # (exit_code -15) before ever writing the log -- 40s gives real margin.

    run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=["-p", "{prompt}"],
        prompt="HELLO",
        timeout_s=40,
        log_path=log,
        env_extra={},
    )

    assert "prompt-was=HELLO" in log.read_text()


def test_env_extra_should_propagate_to_subprocess(tmp_path: Path) -> None:
    script = _bash_script(tmp_path, 'echo "EFFORT=$CLAUDE_CODE_EFFORT_LEVEL"; exit 0')
    log = tmp_path / "out.log"

    run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        # see test_prompt_arg_template_should_substitute_prompt_in_argv: contention headroom
        timeout_s=40,
        log_path=log,
        env_extra={"CLAUDE_CODE_EFFORT_LEVEL": "xhigh"},
    )

    assert "EFFORT=xhigh" in log.read_text()


def test_process_group_should_terminate_descendants_when_killed(
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
    # timeout_s=30 (widened from 15, then from 10, then from 2): under
    # `-n auto` contention on a busy host, a too-tight timeout_s can fire
    # before bash even gets scheduled to fork the grandchild and write its
    # pidfile, killing the pgroup before the thing under test ever ran -- the
    # assertion below then fails for a reason unrelated to the
    # descendants-terminate property it checks (observed: a 10s bound missed
    # this exact class of contention stall and flaked). This script always
    # blocks on `wait` until the timeout fires, so the bound directly sets
    # this test's run time -- 30s (not the 40s used for the trivial-echo
    # siblings in this file) balances >=2-concurrent-gate headroom against
    # not needlessly inflating the parallel pass.

    run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=30,
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


def test_env_extra_should_not_inject_implicit_env_when_empty(
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
        # see test_prompt_arg_template_should_substitute_prompt_in_argv: contention headroom
        timeout_s=40,
        log_path=log,
        env_extra={},
    )

    text = log.read_text()
    assert "AUTOUPDATER=unset" in text
    assert "EFFORT=unset" in text


def test_child_should_execute_in_work_dir_when_run(tmp_path: Path) -> None:
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
        # see test_prompt_arg_template_should_substitute_prompt_in_argv: contention headroom
        timeout_s=40,
        log_path=log,
        env_extra={},
    )

    assert result.exit_code == 0
    assert log.read_text().strip() == str(work.resolve())


def test_stderr_output_should_merge_into_round_log_when_run(tmp_path: Path) -> None:
    """stderr=STDOUT is load-bearing: oauth_fail/network_fail regex-scan stderr
    text out of the round log (contract: hooks.HookContext.agent_log_path)."""
    script = _bash_script(tmp_path, "echo OUT_LINE; echo ERR_MARKER >&2")
    log = tmp_path / "out.log"

    result = run(
        work_dir=tmp_path,
        command=[str(script)],
        prompt_arg_template=[],
        prompt="ignored",
        # see test_prompt_arg_template_should_substitute_prompt_in_argv: contention headroom
        timeout_s=40,
        log_path=log,
        env_extra={},
    )

    assert result.exit_code == 0
    text = log.read_text()
    assert "OUT_LINE" in text
    assert "ERR_MARKER" in text
