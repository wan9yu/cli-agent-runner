"""Tests for _notify.ring / Listener / NullListener -- the FIFO doorbell.

Real FIFOs throughout, under tmp_log_dir -- the fast local-filesystem path
every platform this repo targets supports. NullListener's fake-clock test is
the one exception, pinning virtual-time semantics deterministically.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent_runner import events
from agent_runner._notify import Listener, NullListener, ring
from tests._clock import FakeClock


def _skip_unless_chmod_blocks_write(notify_dir: Path) -> None:
    """chmod 0o555 only actually blocks writes for a non-root process -- under
    root (some CI containers) the permission bits are advisory. Restore the
    writable mode before skipping so tmp_path teardown can still remove the
    directory."""
    if os.access(notify_dir, os.W_OK):
        os.chmod(notify_dir, 0o755)
        pytest.skip("directory permissions do not block writes here (likely running as root)")


def test_listener_should_wake_on_ring(tmp_log_dir: Path):
    with Listener(tmp_log_dir) as listener:
        ring(tmp_log_dir)

        start = time.monotonic()
        woken = listener.wait(5.0)
        elapsed = time.monotonic() - start

        assert woken is True
        assert elapsed < 1.0  # sub-second-tight, not tick-quantized


def test_listener_should_time_out_when_no_ring(tmp_log_dir: Path):
    with Listener(tmp_log_dir) as listener:
        start = time.monotonic()
        woken = listener.wait(0.15)
        elapsed = time.monotonic() - start

        assert woken is False
        assert elapsed < 1.0


def test_ring_should_swallow_when_no_listener(tmp_log_dir: Path):
    ring(tmp_log_dir)


def test_ring_should_unlink_stale_fifo(tmp_log_dir: Path):
    notify_dir = tmp_log_dir / ".notify"
    notify_dir.mkdir()
    stale = notify_dir / "stale.fifo"
    os.mkfifo(stale)

    ring(tmp_log_dir)

    assert not stale.exists()


def test_null_listener_should_sleep_and_return_false():
    fake = FakeClock()

    woken = NullListener().wait(3.0, clock=fake)

    assert woken is False
    assert fake.monotonic() == 3.0


def test_wait_should_coalesce_burst(tmp_log_dir: Path):
    with Listener(tmp_log_dir) as listener:
        ring(tmp_log_dir)
        ring(tmp_log_dir)
        ring(tmp_log_dir)

        first = listener.wait(5.0)
        second = listener.wait(0.15)

        assert first is True
        assert second is False


def test_events_tail_should_wake_on_new_event(tmp_log_dir: Path):
    with Listener(tmp_log_dir) as listener:
        events.emit(tmp_log_dir, events.MONITOR_STARTED)

        woken = listener.wait(5.0)

        assert woken is True


def test_ring_should_not_raise_when_stale_fifo_unlink_fails(tmp_log_dir: Path):
    notify_dir = tmp_log_dir / ".notify"
    notify_dir.mkdir()
    os.mkfifo(notify_dir / "stale.fifo")
    os.chmod(notify_dir, 0o555)
    _skip_unless_chmod_blocks_write(notify_dir)

    try:
        events.emit(tmp_log_dir, events.MONITOR_STARTED)

        written = [p.read_text() for p in tmp_log_dir.glob("events-*.jsonl")]
        assert len(written) == 1
        assert "monitor_started" in written[0]
    finally:
        os.chmod(notify_dir, 0o755)


def test_listener_exit_should_not_raise_when_unlink_fails(tmp_log_dir: Path):
    notify_dir = tmp_log_dir / ".notify"
    listener = Listener(tmp_log_dir)
    listener.__enter__()
    os.chmod(notify_dir, 0o555)
    _skip_unless_chmod_blocks_write(notify_dir)

    try:
        listener.__exit__(None, None, None)
    finally:
        os.chmod(notify_dir, 0o755)
