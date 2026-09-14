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

import sys
import time
import types
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_runner import config as ar_config
from agent_runner import schedule as ar_schedule
from agent_runner._notify import Listener, ring
from agent_runner._throttle import _interruptible_sleep
from agent_runner.cli import _serve_round, serve_cmd
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


def test_spawn_round_should_not_busy_spin_after_doorbell_ring(tmp_path, monkeypatch):
    """The FIFO doorbell is level-triggered: a byte that _spawn_round's mid-round
    wait_exit(extra_fds=(doorbell_fd,)) sees but never drains leaves every
    subsequent select() instantly ready, busy-spinning the loop at 100% CPU
    for the rest of the round instead of blocking until the round actually
    exits. Reproduces the exact scenario a `serve stop`/SIGTERM (or a
    cross-process ring()) mid-round hits: one ring queued BEFORE the round
    starts, then a real ~1.5s child. Counts wait_exit calls -- undrained, this
    explodes into hundreds of thousands of calls (measured: 445,795); drained,
    it's ~2 (one immediate "woken", one blocking "exited")."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(1.5)"]
    calls = {"n": 0}
    real_wait_exit = _serve_round.wait_exit

    def counting_wait_exit(*args, **kwargs):
        calls["n"] += 1
        return real_wait_exit(*args, **kwargs)

    monkeypatch.setattr(_serve_round, "wait_exit", counting_wait_exit)

    with Listener(log_dir) as listener:
        ring(log_dir)

        rc = _serve_round._spawn_round(
            argv,
            log_dir / "round-1.log",
            {},
            timeout_s=300,
            round_num=1,
            doorbell_fd=listener.fd,
        )

    assert rc == 0
    assert calls["n"] < 20  # undrained: ~445,795 in the reproduction
