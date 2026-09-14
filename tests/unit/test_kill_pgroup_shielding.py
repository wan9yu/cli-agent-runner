"""`_kill_pgroup`'s grace wait is fd-driven (`wait_exit`), KeyboardInterrupt-
shielded so a re-entrant SIGTERM (round_cmd's handler turns every SIGTERM into
a fresh KeyboardInterrupt for the process's whole life) can't skip the SIGKILL
escalation. The v0.3.3 dark-code lesson: a review that only checks the
MECHANISM (an arg value) can miss a broken PROPERTY. These tests verify the
end-to-end behavior -- the retry happens, grace is not inflated, and SIGKILL
still fires -- driving `wait_exit`'s poll fallback (FakeClock) so a
re-entrant interrupt can be injected deterministically without any real
wall-clock wait or a real signal."""

from __future__ import annotations

import signal

from agent_runner import _procwait, agent_runtime
from tests._clock import FakeClock


class _NeverExitsProc:
    """A pgroup leader that never dies from the SIGTERM/SIGKILL this fake
    test double for `os.killpg` never actually delivers -- `poll()` always
    reports "still running" so both of `_kill_pgroup`'s grace waits genuinely
    exhaust their deadlines rather than short-circuiting on an "exited"."""

    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.returncode = None  # live/unreaped, like a running Popen

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise AssertionError("proc never exits in this test -- wait() must not be reached")


def _clock_raising_on_call(raise_on: set[int]) -> tuple[FakeClock, dict]:
    """A FakeClock whose `sleep` raises KeyboardInterrupt on the given
    (1-indexed) call numbers -- simulating a re-entrant SIGTERM landing
    exactly then -- and advances virtual time normally otherwise."""
    clock = FakeClock()
    real_sleep = clock.sleep
    calls = {"n": 0}

    def flaky_sleep(seconds):
        calls["n"] += 1
        if calls["n"] in raise_on:
            raise KeyboardInterrupt("re-entrant SIGTERM during grace")
        real_sleep(seconds)

    clock.sleep = flaky_sleep
    return clock, calls


def test_kill_pgroup_should_not_inflate_grace_on_reentrant_sigterm(monkeypatch):
    """A re-entrant SIGTERM landing PARTWAY through the grace window (after 2
    of its virtual seconds have already elapsed, not at the very start) must
    not push the total grace past REAP_GRACE_S -- a buggy retry that
    recomputes `deadline = clock.monotonic() + REAP_GRACE_S` on each retry
    (instead of reusing the one fixed absolute deadline) would inflate this
    to ~7s; this test fails under that bug and passes under the correct
    fixed-deadline retry. SIGKILL must still fire right after grace ends."""
    monkeypatch.setattr(_procwait, "exit_fd", lambda proc: None)
    monkeypatch.setattr(agent_runtime, "_snapshot_stray_descendants", lambda proc: [])
    monkeypatch.setattr(agent_runtime, "_kill_stray_descendants", lambda stray: None)
    proc = _NeverExitsProc()
    clock, calls = _clock_raising_on_call({3})  # ~2s of real grace already elapsed
    killpg_calls: list[tuple[int, int, float]] = []

    def spy_killpg(pgid, sig):
        killpg_calls.append((pgid, sig, clock.monotonic()))

    monkeypatch.setattr(agent_runtime.os, "killpg", spy_killpg)

    agent_runtime._kill_pgroup(proc, clock)

    assert calls["n"] >= 4, "the shield must retry after the re-entrant interrupt"
    sigterm_call, sigkill_call = killpg_calls[0], killpg_calls[1]
    assert sigterm_call[:2] == (proc.pid, signal.SIGTERM)
    assert sigkill_call[:2] == (proc.pid, signal.SIGKILL)
    grace_elapsed = sigkill_call[2]  # virtual time (since T0) when SIGKILL fired
    assert grace_elapsed <= agent_runtime.REAP_GRACE_S, (
        f"grace window inflated by the re-entrant interrupt: {grace_elapsed} > "
        f"{agent_runtime.REAP_GRACE_S} (deadline was likely recomputed on retry)"
    )


def test_kill_pgroup_should_propagate_keyboardinterrupt_through_wait_exit(monkeypatch):
    """`wait_exit` must not swallow KeyboardInterrupt -- if it did, the shield's
    `except KeyboardInterrupt: continue` would never fire and this would
    silently degrade to a single, unshielded wait. Two re-entrant interrupts
    in a row must both reach and be absorbed by the shield, and the kill
    still runs afterward -- observed via the retry count, not the deadline
    argument, so a future change that lets wait_exit swallow the interrupt
    (making the shield dark code) is caught here."""
    monkeypatch.setattr(_procwait, "exit_fd", lambda proc: None)
    monkeypatch.setattr(agent_runtime, "_snapshot_stray_descendants", lambda proc: [])
    monkeypatch.setattr(agent_runtime, "_kill_stray_descendants", lambda stray: None)
    proc = _NeverExitsProc()
    clock, calls = _clock_raising_on_call({1, 2})
    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        agent_runtime.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig))
    )

    agent_runtime._kill_pgroup(proc, clock)

    assert calls["n"] >= 3, "both re-entrant interrupts must have reached the shield"
    assert (proc.pid, signal.SIGKILL) in killpg_calls, (
        "SIGKILL must still fire after the interrupts, never skipped"
    )
