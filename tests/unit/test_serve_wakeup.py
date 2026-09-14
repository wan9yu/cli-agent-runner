"""Serve wakeup fd (doorbell): the ONE new mechanism Task 5 wires in -- a real
``Listener``'s ``.wait`` wakes ``_pause_poll``/``_interruptible_sleep`` promptly
on a ``ring()`` (standing in for a SIGTERM/SIGINT landing on the SAME fd via
``signal.set_wakeup_fd``, see ``serve_cmd._open_serve_doorbell``) instead of
riding out the full chunk/duration. The default -- ``NULL_LISTENER``, what
every pre-existing caller still gets -- keeps degrading to a plain
``clock.sleep``/injected ``sleep_fn``, so nothing already using these two
functions changes.

Real FIFOs throughout (matching test_notify.py's own style): ``ring()`` before
the wait call queues the wake byte in the kernel pipe buffer, so no thread/race
is needed to prove the prompt-wake property deterministically.
"""

from __future__ import annotations

import time

from agent_runner._notify import Listener, ring
from agent_runner._throttle import _interruptible_sleep
from agent_runner.cli._serve_round import _pause_poll
from tests._clock import FakeClock


def test_pause_poll_should_wake_promptly_when_ring_arrives_before_wait(tmp_log_dir):
    with Listener(tmp_log_dir) as listener:
        ring(tmp_log_dir)
        stop = {"requested": False}
        calls = {"n": 0}

        def runnable_fn() -> bool:
            calls["n"] += 1
            return calls["n"] > 1  # False on the pre-wake check, True right after the wake

        start = time.monotonic()
        woken = _pause_poll(stop, None, runnable_fn, time.sleep, 30, listener=listener)
        elapsed = time.monotonic() - start

        assert woken is True
        assert calls["n"] == 2
        assert elapsed < 1.0  # woke on the ring, not the 30s chunk


def test_interruptible_sleep_should_wake_promptly_when_ring_arrives_before_wait(tmp_log_dir):
    with Listener(tmp_log_dir) as listener:
        ring(tmp_log_dir)
        stop = {"requested": False}

        start = time.monotonic()
        interrupted = _interruptible_sleep(30, stop, chunk_s=30, listener=listener)
        elapsed = time.monotonic() - start

        assert interrupted is False  # total_s exhausted -- the ring only shortened the WAIT
        assert elapsed < 1.0  # ...not the accounting: listener.wait returned in ms, not 30s


def test_pause_poll_should_fall_through_to_injected_sleep_fn_when_default_listener():
    stop = {"requested": False}
    calls = {"n": 0}
    slept: list[float] = []

    def runnable_fn() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    woken = _pause_poll(stop, None, runnable_fn, slept.append, 30)

    assert woken is True
    assert slept == [30, 30]  # NULL_LISTENER default -> the else-branch sleep_fn, unchanged


def test_interruptible_sleep_should_sleep_full_duration_when_default_listener():
    clock = FakeClock()

    interrupted = _interruptible_sleep(90, {"requested": False}, clock=clock, chunk_s=30)

    assert interrupted is False
    assert clock.slept == [30.0, 30.0, 30.0]  # NULL_LISTENER default -> plain clock.sleep
