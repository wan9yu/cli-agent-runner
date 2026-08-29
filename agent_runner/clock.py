"""Injectable clock — the ONE home of wall-clock / monotonic / sleep primitives.

Every other module takes a :class:`Clock` (or reads the shared
:data:`SYSTEM_CLOCK` for cosmetic timestamps); raw ``time.time()`` /
``time.monotonic()`` / ``time.sleep()`` / ``datetime.now()`` must NOT appear
anywhere else. Tests pass a ``FakeClock`` (``tests/_clock.py``) with advanceable
virtual time to pin an exact instant instead of racing the wall clock. The
"no raw time outside this file" rule is pinned by
``tests/invariants/test_no_raw_time.py``.

Why an object, not scattered ``time.time()``: five distinct time kinds
(epoch / monotonic / sleep / UTC-now / tz-now) collapse to one seam, so a test
controls all of them with a single ``FakeClock`` instead of a monkeypatch per
call site. ``SYSTEM_CLOCK`` is a stable singleton — safe as a parameter default
(unlike a ``time.time()`` default, which would freeze at import).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    """The time surface the codebase depends on. ``RealClock`` in production, a
    ``FakeClock`` in tests. Parsing (``fromisoformat``) is a pure function and is
    deliberately NOT here — only *reading the current time* is injectable."""

    def epoch(self) -> float:
        """Wall-clock seconds since the epoch (``time.time()``)."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds for measuring durations (``time.monotonic()``)."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for ``seconds`` (``time.sleep()``)."""
        ...

    def now_utc(self) -> datetime:
        """Timezone-aware current time in UTC."""
        ...

    def now_in_zone(self, tz_name: str | None) -> datetime:
        """Timezone-aware now in ``tz_name`` (``None`` → host local time)."""
        ...


class RealClock:
    """Production clock — the sole caller of the stdlib time/datetime primitives."""

    def epoch(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def now_in_zone(self, tz_name: str | None) -> datetime:
        if tz_name is None:
            return datetime.now().astimezone()
        return datetime.now(ZoneInfo(tz_name))


# Shared production instance. Used as the default for injectable functions (a
# stable reference, so it is a safe parameter default) and read directly by the
# cosmetic timestamp paths (events/metrics) that do not thread a clock.
SYSTEM_CLOCK: Clock = RealClock()
