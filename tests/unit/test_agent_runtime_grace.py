"""Tests for max_grace_after_result_s HUNG defense (0.1.31+)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

from agent_runner.agent_runtime import run


def _write_fake_script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake.sh"
    p.write_text(f"#!/bin/bash\nset -e\n{body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_grace_kill_fires_when_result_then_idle(tmp_path):
    """Agent writes type=result then becomes a childless sleeper (exec) -> no
    live workers -> reaped within grace + tick latency."""
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result","is_error":false}\'\nexec sleep 5\n',
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
    assert result.grace_kill_children == []
    assert result.duration_s < 4


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


def test_live_children_empty_when_no_children():
    from agent_runner.agent_runtime import _live_children

    p = subprocess.Popen(["sleep", "3"], start_new_session=True)
    try:
        assert _live_children(p) == ([], [])
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_live_children_lists_backgrounded_child():
    from agent_runner.agent_runtime import _live_children

    p = subprocess.Popen(["bash", "-c", "sleep 30 & wait"], start_new_session=True)
    try:
        time.sleep(0.5)  # let the backgrounded child spawn
        live, ignored = _live_children(p)
        assert any("sleep" in k for k in live)
        assert ignored == []
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_live_children_empty_when_process_gone():
    from agent_runner.agent_runtime import _live_children

    p = subprocess.Popen(["true"])
    p.wait()
    assert _live_children(p) == ([], [])  # NoSuchProcess swallowed


def test_grace_extended_when_result_but_child_running(tmp_path):
    """Agent emits result then backgrounds a long child -> live worker -> NOT
    grace-killed; round_timeout_s (wall) reaps it instead; extended fired once."""
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result"}\'\nsleep 30 &\nwait\n',
    )
    log_path = tmp_path / "round.log"
    extended = []
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=4,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
        on_grace_extended=lambda live, ignored: extended.append((live, ignored)),
    )
    assert result.killed_for_grace is False  # spared by liveness
    assert result.timed_out is True  # round_timeout_s backstop reaped it
    assert len(extended) == 1  # emitted once, not per-tick
    live, ignored = extended[0]
    assert any("sleep" in k for k in live)


def test_grace_kill_after_child_exits_then_idle(tmp_path):
    """Live child first (extend), child exits, agent becomes childless (exec)
    -> next tick reaps via grace (well before wall timeout)."""
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result"}\'\nsleep 2 &\nwait\nexec sleep 30\n',
    )
    log_path = tmp_path / "round.log"
    extended = []
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=10,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
        on_grace_extended=lambda live, ignored: extended.append((live, ignored)),
    )
    assert result.killed_for_grace is True  # reaped after child exited
    assert result.duration_s < 6  # ~2s child + reap, well under timeout
    assert len(extended) == 1


def test_live_children_splits_on_ignore_pattern():
    """A child whose cmdline matches an ignore pattern goes to 'ignored', others to 'live'."""
    from agent_runner.agent_runtime import _live_children

    # Use exec -a to rename a child's argv[0] to a matchable name.
    p = subprocess.Popen(
        ["bash", "-c", "exec -a snapshot-bash-xyz sleep 30 & sleep 30 & wait"],
        start_new_session=True,
    )
    try:
        time.sleep(0.5)
        live, ignored = _live_children(p, ignore_patterns=[re.compile(r"snapshot-bash-")])
        # One child should match the ignore pattern; the plain sleep goes to live.
        assert any("snapshot-bash-" in c or "sleep" in c for c in ignored)
        assert any("sleep" in c for c in live)
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_live_children_no_patterns_preserves_0138_behavior():
    """ignore_patterns=None -> tuple shape, but everything alive goes to 'live'."""
    from agent_runner.agent_runtime import _live_children

    p = subprocess.Popen(["bash", "-c", "sleep 30 & wait"], start_new_session=True)
    try:
        time.sleep(0.5)
        live, ignored = _live_children(p)  # default None
        assert ignored == []
        assert any("sleep" in c for c in live)
    finally:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def test_grace_kill_fires_when_only_ignored_helper_alive(tmp_path):
    """The 0.1.38 'persistent-helper caveat' fix: with a matching pattern, a round
    whose only live descendant is the ignored helper is reaped at grace, not
    deferred to round_timeout_s."""
    # Fake agent: emit type=result, then exec into a 'helper' (no children remain).
    # The exec replaces the agent process itself (not a child); psutil.children()
    # gives descendants only — so after exec the agent has NO children -> reap.
    script = _write_fake_script(
        tmp_path,
        'echo \'{"type":"result"}\'\nexec -a snapshot-bash-test sleep 30\n',
    )
    log_path = tmp_path / "round.log"
    extended = []
    result = run(
        command=[str(script)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=10,
        log_path=log_path,
        env_extra={},
        max_grace_after_result_s=1,
        on_grace_extended=lambda live, ignored: extended.append((live, ignored)),
        grace_kill_ignore_patterns=[re.compile(r"snapshot-bash-")],
    )
    assert result.killed_for_grace is True
    assert result.duration_s < 4
    assert extended == []  # no extension emitted; reaped directly
