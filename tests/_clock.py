"""FakeClock — advanceable virtual time for tests.

Lives in tests/ (not agent_runner/) so it has no production consumer to keep
vulture happy. One instance pins epoch, monotonic, UTC-now and tz-now together;
``sleep`` advances virtual time, so a chunked pause loop terminates deterministically
instead of needing a monkeypatched ``stop`` flag.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


class FakeClock:
    def __init__(self, epoch: float = 1_700_000_000.0, *, monotonic: float = 0.0):
        self._epoch = epoch
        self._mono = monotonic
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
