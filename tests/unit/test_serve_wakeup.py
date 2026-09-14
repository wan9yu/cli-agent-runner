"""Serve wakeup fd (doorbell): the ONE new mechanism Task 5 wires in -- a real
``Listener``'s ``.wait`` wakes ``_pause_poll``/``_interruptible_sleep`` promptly
on a ``ring()`` (standing in for a SIGTERM/SIGINT landing on the SAME fd via
``signal.set_wakeup_fd``, see ``serve_cmd._open_serve_doorbell``) so a genuine
stop (``stop["requested"]``/``should_stop()``) lands within milliseconds instead
of riding out the full chunk/duration. A ring with NO stop is a different story
for ``_interruptible_sleep``: it only shortens the individual ``listener.wait``
call, not the sleep's total duration -- the function credits actual elapsed
monotonic time per slice, so a spurious/advisory ring (e.g. an unrelated
``events.emit`` on the same ``log_dir``) cannot collapse a caller's intended
delay (see ``_interruptible_sleep``'s docstring and the serve restart/crash
back-off delay it guards). The default -- ``NULL_LISTENER``, what every
pre-existing caller still gets -- keeps degrading to a plain ``clock.sleep``/
injected ``sleep_fn``, so nothing already using these two functions changes.

Real FIFOs throughout (matching test_notify.py's own style): ``ring()`` before
the wait call queues the wake byte in the kernel pipe buffer, so no thread/race
is needed to prove the prompt-wake property deterministically.
"""

from __future__ import annotations

import time
import types
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_runner import config as ar_config
from agent_runner import schedule as ar_schedule
from agent_runner._notify import Listener, ring
from agent_runner._throttle import _interruptible_sleep
from agent_runner.cli import serve_cmd
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


def test_interruptible_sleep_should_not_collapse_when_a_spurious_ring_lands(tmp_log_dir):
    """A pre-queued ring with no stop must NOT shorten the sleep: this is the serve
    restart/crash back-off delay's whole anti-hammering purpose (serve_cmd.cmd()
    passes the live doorbell listener here). A stray event byte -- another emit
    before this call even started, or a concurrent monitor -- wakes listener.wait
    early, but _interruptible_sleep now credits only the ACTUAL elapsed monotonic
    time from that wait, not the full intended nap, so the total duration still
    takes ~the full total_s. total_s is kept short (well under chunk_s, so this is
    a single slice) purely so the test runs fast."""
    total_s = 0.4
    with Listener(tmp_log_dir) as listener:
        ring(tmp_log_dir)
        stop = {"requested": False}

        start = time.monotonic()
        interrupted = _interruptible_sleep(total_s, stop, chunk_s=30, listener=listener)
        elapsed = time.monotonic() - start

        assert interrupted is False
        assert elapsed >= total_s * 0.8  # the ring drained but did NOT collapse the wait


def test_interruptible_sleep_should_return_promptly_when_stop_is_requested(tmp_log_dir):
    with Listener(tmp_log_dir) as listener:
        stop = {"requested": True}

        start = time.monotonic()
        interrupted = _interruptible_sleep(30, stop, chunk_s=30, listener=listener)
        elapsed = time.monotonic() - start

        assert interrupted is True
        assert elapsed < 1.0  # a real stop still cuts the sleep short, ring or no ring


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


def _paused_schedule_cfg(pause_windows: list[str]):
    return types.SimpleNamespace(
        schedule=ar_config.ScheduleConfig(
            timezone="Asia/Shanghai",
            pause_windows=tuple(ar_schedule.parse_window(w) for w in pause_windows),
        ),
        runtime=types.SimpleNamespace(stop_file=None),
    )


def test_pause_should_wake_early_on_ring_when_listener_is_live(tmp_log_dir):
    """A production PAUSE path (not just the mid-round wait), driven through
    serve_cmd._maybe_pause_for_schedule -- the wrapper cmd() actually calls
    with its live listener -- wakes on a pre-queued ring() instead of riding
    out its 30s chunk. The window stays closed across the wrapper's own
    ``evaluate`` check AND the poll's first ``runnable_fn`` check (both hour
    10, inside the 09:00-12:00 pause window) so the loop genuinely reaches
    ``listener.wait`` before the window opens (hour 13, third call)."""
    with Listener(tmp_log_dir) as listener:
        ring(tmp_log_dir)
        cfg = _paused_schedule_cfg(["09:00-12:00"])
        stop = {"requested": False}
        hours = iter([10, 10, 13])

        def now_fn(_tz):
            return datetime(2026, 8, 22, next(hours), 0, tzinfo=ZoneInfo("Asia/Shanghai"))

        start = time.monotonic()
        paused = serve_cmd._maybe_pause_for_schedule(
            cfg, tmp_log_dir, stop, now_fn=now_fn, chunk_s=30, listener=listener
        )
        elapsed = time.monotonic() - start

        assert paused is True
        assert elapsed < 1.0  # the doorbell drained the pre-queued ring, not a 30s chunk


def test_pause_should_sleep_full_chunk_when_default_listener_and_no_ring(tmp_log_dir):
    """Byte-identical companion: with no listener passed (NULL_LISTENER
    default), the SAME wrapper still advances via the injected sleep_fn --
    exactly the pre-doorbell behavior -- proving the live-listener wake above
    is additive, not a change to the default path."""
    cfg = _paused_schedule_cfg(["09:00-12:00"])
    stop = {"requested": False}
    hours = iter([10, 10, 13])
    slept: list[float] = []

    def now_fn(_tz):
        return datetime(2026, 8, 22, next(hours), 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    paused = serve_cmd._maybe_pause_for_schedule(
        cfg, tmp_log_dir, stop, now_fn=now_fn, sleep_fn=slept.append, chunk_s=30
    )

    assert paused is True
    assert slept == [30]
