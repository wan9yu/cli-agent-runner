"""Tests for _procwait.exit_fd / wait_exit.

Real subprocesses throughout -- exercised on whichever fast path the host
actually supports (macOS kqueue locally; Linux pidfd on CI), transparently,
by calling the same public functions. The fallback path is forced explicitly
by monkeypatching exit_fd to None, per the module's own documented contract.
"""

from __future__ import annotations

import os
import subprocess
import time

from agent_runner import _procwait
from agent_runner._procwait import exit_fd, wait_exit
from agent_runner.clock import SYSTEM_CLOCK


def test_exit_fd_should_return_a_readable_fd_for_a_live_process_when_invoked():
    proc = subprocess.Popen(["sleep", "5"])

    try:
        fd = exit_fd(proc)

        assert fd is not None
        os.close(fd)
    finally:
        proc.terminate()
        proc.wait()


def test_wait_exit_should_return_exited_when_proc_exits():
    proc = subprocess.Popen(["sleep", "0.2"])

    outcome = wait_exit(proc, deadline=SYSTEM_CLOCK.monotonic() + 5)

    assert outcome == "exited"
    assert proc.returncode is None  # wait_exit itself did not reap

    rc = proc.poll()  # the test reaps
    assert rc is not None


def test_wait_exit_should_short_circuit_to_exited_without_reopening_a_reaped_pid_when_invoked(
    monkeypatch,
):
    proc = subprocess.Popen(["true"])
    proc.wait()  # this owner reaps (as Popen.terminate()'s poll does) -> pid freed, maybe reused

    def _fail_if_called(_proc):
        raise AssertionError("wait_exit must not re-open exit_fd on an already-reaped pid")

    monkeypatch.setattr(_procwait, "exit_fd", _fail_if_called)

    outcome = wait_exit(proc, deadline=SYSTEM_CLOCK.monotonic() + 5)

    assert outcome == "exited"


def test_wait_exit_should_return_timeout_when_deadline_passes_before_exit():
    proc = subprocess.Popen(["sleep", "5"])

    try:
        start = time.monotonic()
        outcome = wait_exit(proc, deadline=SYSTEM_CLOCK.monotonic() + 0.15)
        elapsed = time.monotonic() - start

        assert outcome == "timeout"
        assert elapsed < 1.0  # sub-second-tight, not tick-quantized
    finally:
        proc.terminate()
        proc.wait()


def test_wait_exit_should_report_exited_when_proc_already_dead_at_deadline():
    """A boundary tie: the exit fd can be ready in the SAME select() call
    that also finds its deadline already reached. The already-dead proc must
    win that tie -- a caller (e.g. the mid-round mem-check/round-budget
    check in _spawn_round) must never treat a round that has already
    finished on its own as a timeout worth acting on."""
    proc = subprocess.Popen(["sleep", "0.05"])
    try:
        time.sleep(0.3)  # let it exit for real, without reaping it (no poll()/wait() yet)

        outcome = wait_exit(proc, deadline=SYSTEM_CLOCK.monotonic())

        assert outcome == "exited"
    finally:
        proc.wait()  # the test reaps


def test_wait_exit_should_fall_back_to_poll_when_exit_fd_none(monkeypatch):
    monkeypatch.setattr(_procwait, "exit_fd", lambda proc: None)
    monkeypatch.setattr(_procwait, "_POLL_TICK_S", 0.05)
    proc = subprocess.Popen(["sleep", "0.2"])

    outcome = wait_exit(proc, deadline=SYSTEM_CLOCK.monotonic() + 5)

    assert outcome == "exited"
    assert proc.returncode == 0  # the poll fallback reaps synchronously, unlike the fast path


def test_wait_exit_should_return_timeout_during_poll_fallback_with_no_extra_fds_when_invoked(
    monkeypatch,
):
    monkeypatch.setattr(_procwait, "exit_fd", lambda proc: None)
    monkeypatch.setattr(_procwait, "_POLL_TICK_S", 0.05)
    proc = subprocess.Popen(["sleep", "5"])

    try:
        outcome = wait_exit(proc, deadline=SYSTEM_CLOCK.monotonic() + 0.15)

        assert outcome == "timeout"
    finally:
        proc.terminate()
        proc.wait()


def test_wait_exit_should_degrade_to_poll_when_select_raises_valueerror(monkeypatch):
    def _raise_value_error(*_args, **_kwargs):
        raise ValueError("filedescriptor out of range in select()")

    monkeypatch.setattr(_procwait.select, "select", _raise_value_error)
    monkeypatch.setattr(_procwait, "_POLL_TICK_S", 0.05)
    proc = subprocess.Popen(["sleep", "0.2"])

    outcome = wait_exit(proc, deadline=SYSTEM_CLOCK.monotonic() + 5)

    assert outcome == "exited"
    assert proc.returncode == 0
