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
