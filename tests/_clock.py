"""Test clocks — FakeClock and PollLoopClock, both the full Clock surface.

Lives in tests/ (not agent_runner/) so it has no production consumer to keep
vulture happy.

* ``FakeClock`` pins epoch, monotonic, UTC-now and tz-now together; ``sleep``
  advances virtual time, so a chunked pause loop terminates deterministically
  instead of needing a monkeypatched ``stop`` flag. Use this for unit tests
  with no live child.
* ``PollLoopClock`` is a live-child poll-fallback double, not a second kind
  of time. ``monotonic()`` jumps ``step`` per call so a 10s sample interval
  elapses without waiting 10s; ``sleep()`` is a real short block so the
  child can actually exit. Do not substitute FakeClock there —
  FakeClock.sleep never yields to a live child (the child's clock is the
  kernel's, not ours).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


class FakeClock:
    def __init__(self, epoch: float = 1_700_000_000.0, *, start: str | None = None):
        """``start``, when given, is an ISO-8601 UTC timestamp (e.g.
        ``"2026-09-16T00:00:00Z"``) converted to ``epoch`` -- a more readable
        alternative to a raw epoch float for tests that pin a calendar date."""
        if start is not None:
            epoch = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
        self._epoch = epoch
        self._mono = 0.0
        self.slept: list[float] = []

    # --- Clock surface -------------------------------------------------------
    def epoch(self) -> float:
        return self._epoch

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)  # virtual time moves so pause loops make progress

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self._epoch, UTC)

    def now_in_zone(self, tz_name: str | None) -> datetime:
        if tz_name is None:
            return self.now_utc().astimezone()
        return self.now_utc().astimezone(ZoneInfo(tz_name))

    # --- test controls -------------------------------------------------------
    def advance(self, seconds: float) -> None:
        self._epoch += seconds
        self._mono += seconds

    def warp_epoch(self, delta: float) -> None:
        """Shift the WALL clock only (simulates an NTP step); monotonic unmoved —
        so a deadline measured on monotonic must be unaffected."""
        self._epoch += delta


class PollLoopClock:
    """Live-child poll-fallback double: fake monotonic, real sleep.

    ``monotonic()`` advances by ``step`` on every call so a ~10s sample interval
    elapses without waiting ~10 real seconds. ``sleep()`` is a real short block
    and does **not** advance monotonic — the poll fallback's per-tick pacing,
    so a live child gets repeated chances to exit. Wall methods (``epoch`` /
    ``now_utc`` / ``now_in_zone``) sit on a fixed epoch so this is a full
    ``Clock``, not a two-method stub.

    Do not use FakeClock here. The child process uses the kernel clock.
    """

    def __init__(self, step: float = 5.0, epoch: float = 1_700_000_000.0):
        self._mono = 0.0
        self._step = step
        self._epoch = epoch

    def epoch(self) -> float:
        return self._epoch

    def monotonic(self) -> float:
        self._mono += self._step
        return self._mono

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self._epoch, UTC)

    def now_in_zone(self, tz_name: str | None) -> datetime:
        if tz_name is None:
            return self.now_utc().astimezone()
        return self.now_utc().astimezone(ZoneInfo(tz_name))


class FakeRoundProc:
    """Popen stand-in when tests script ``wait_exit``. ``terminate()`` marks
    the leader killed so the next ``wait_exit`` returns ``exited``."""

    pid = 4242

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = -15 if self.terminated else 0
        return self.returncode


def install_scripted_round(
    monkeypatch,
    clock: FakeClock,
    *,
    exit_after_timeouts: int | None = None,
) -> None:
    """Decision-test seam: no live child, FakeClock owns TIME, wait_exit owns WAIT.

    Each ``timeout`` advances ``clock`` to ``deadline`` (as a real blocking
    wait_exit would). After ``exit_after_timeouts`` timeouts the next call
    returns ``exited`` with rc 0. ``terminate()`` makes the next wait_exit
    return ``exited`` (covers ``_terminate_round`` grace waits). Do not use
    this for OS plumbing tests.
    """
    from agent_runner.cli import _serve_round

    timeouts = {"n": 0}

    def _popen(*_a, **_k) -> FakeRoundProc:
        return FakeRoundProc()

    def _wait_exit(proc, *, deadline, clock=clock, wake_fd=None):
        if getattr(proc, "terminated", False) or proc.returncode is not None:
            if proc.returncode is None:
                proc.returncode = -15
            return "exited"
        remaining = deadline - clock.monotonic()
        if remaining > 0:
            clock.advance(remaining)
        timeouts["n"] += 1
        if exit_after_timeouts is not None and timeouts["n"] > exit_after_timeouts:
            proc.returncode = 0
            return "exited"
        return "timeout"

    monkeypatch.setattr(_serve_round.subprocess, "Popen", _popen)
    monkeypatch.setattr(_serve_round, "wait_exit", _wait_exit)
    monkeypatch.setattr(_serve_round, "_snapshot_stray_descendants", lambda proc: [])
    monkeypatch.setattr(_serve_round.os, "killpg", lambda *_a, **_k: None)
