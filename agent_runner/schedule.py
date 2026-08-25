"""Time-window scheduling core (pure). The serve loop uses this to decide,
between rounds, whether to run the next round now or idle-sleep until a
configured window opens. All functions are clock-injectable for testing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_WINDOW_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_MINUTES_PER_DAY = 24 * 60
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_SEARCH_HORIZON_MIN = 8 * _MINUTES_PER_DAY  # weekday-scoped run_windows can pause a full weekend


def _parse_weekday_spec(spec: str) -> frozenset[int]:
    days: set[int] = set()
    for token in spec.split(","):
        token = token.strip().lower()
        if not token:
            raise ValueError(f"empty weekday token in {spec!r}")
        if "-" in token:
            a, _, b = token.partition("-")
            if a not in _WEEKDAYS or b not in _WEEKDAYS:
                raise ValueError(f"invalid weekday range {token!r}")
            start, end = _WEEKDAYS[a], _WEEKDAYS[b]
            if start > end:
                raise ValueError(f"weekday range must be ascending Mon..Sun: {token!r}")
            days.update(range(start, end + 1))
        elif token not in _WEEKDAYS:
            raise ValueError(f"invalid weekday {token!r}")
        else:
            days.add(_WEEKDAYS[token])
    return frozenset(days)


@dataclass(frozen=True)
class Window:
    start_min: int  # minutes from midnight, [0, 1440)
    end_min: int  # minutes from midnight, (0, 1440]
    label: str  # original "[WEEKDAYS ]HH:MM-HH:MM" for event payloads
    days: frozenset[int] = frozenset()  # empty = every day

    def contains(self, weekday: int, minute: int) -> bool:
        if self.start_min < self.end_min:
            in_time = self.start_min <= minute < self.end_min
        else:  # wraps past midnight
            in_time = minute >= self.start_min or minute < self.end_min
        if not in_time:
            return False
        if not self.days:
            return True
        start_day = weekday
        if self.start_min >= self.end_min and minute < self.end_min:
            start_day = (weekday - 1) % 7  # wrapped tail belongs to the start day
        return start_day in self.days


def _to_minutes(h: int, m: int, *, allow_24: bool, where: str) -> int:
    if h == 24:
        if not allow_24 or m != 0:
            raise ValueError(f"{where}: 24:00 is only valid as an end bound")
        return _MINUTES_PER_DAY
    if not 0 <= h <= 23:
        raise ValueError(f"{where}: hour {h} out of range")
    if not 0 <= m <= 59:
        raise ValueError(f"{where}: minute {m} out of range")
    return h * 60 + m


def parse_window(s: str) -> Window:
    spec = s.strip()
    days: frozenset[int] = frozenset()
    parts = spec.split(None, 1)
    if len(parts) == 2 and _WINDOW_RE.match(parts[1]):
        days = _parse_weekday_spec(parts[0])
        time_part = parts[1]
    else:
        time_part = spec
    match = _WINDOW_RE.match(time_part)
    if not match:
        raise ValueError(f"invalid window {s!r}: expected [WEEKDAYS ]HH:MM-HH:MM")
    sh, sm, eh, em = (int(g) for g in match.groups())
    start = _to_minutes(sh, sm, allow_24=False, where=f"window {s!r} start")
    end = _to_minutes(eh, em, allow_24=True, where=f"window {s!r} end")
    if start == end:
        raise ValueError(f"invalid window {s!r}: start equals end (zero-length)")
    return Window(start_min=start, end_min=end, label=s, days=days)


def _minute_of_day(now_local: datetime) -> int:
    return now_local.hour * 60 + now_local.minute


def should_run(
    now_local: datetime,
    *,
    run_windows: list[Window],
    pause_windows: list[Window],
) -> bool:
    minute = _minute_of_day(now_local)
    weekday = now_local.weekday()
    in_run = (not run_windows) or any(w.contains(weekday, minute) for w in run_windows)
    in_pause = any(w.contains(weekday, minute) for w in pause_windows)
    return in_run and not in_pause


def next_resume_at(
    now_local: datetime,
    *,
    run_windows: list[Window],
    pause_windows: list[Window],
) -> datetime | None:
    """First minute-boundary within the next 8 days where should_run flips True.
    Called only while currently paused; returns None if no window opens in 8 days."""
    base = now_local.replace(second=0, microsecond=0)
    for k in range(1, _SEARCH_HORIZON_MIN + 1):
        cand = base + timedelta(minutes=k)
        if should_run(cand, run_windows=run_windows, pause_windows=pause_windows):
            return cand
    return None


def _active_window_label(
    now_local: datetime,
    *,
    run_windows: list[Window],
    pause_windows: list[Window],
) -> str:
    minute = _minute_of_day(now_local)
    weekday = now_local.weekday()
    for w in pause_windows:
        if w.contains(weekday, minute):
            return w.label
    return "outside run_windows"


@dataclass(frozen=True)
class PauseDecision:
    paused: bool
    resume_at: datetime | None
    active_window: str | None


def evaluate(
    *,
    run_windows: list[Window],
    pause_windows: list[Window],
    now_local: datetime,
) -> PauseDecision:
    if should_run(now_local, run_windows=run_windows, pause_windows=pause_windows):
        return PauseDecision(paused=False, resume_at=None, active_window=None)
    return PauseDecision(
        paused=True,
        resume_at=next_resume_at(now_local, run_windows=run_windows, pause_windows=pause_windows),
        active_window=_active_window_label(
            now_local, run_windows=run_windows, pause_windows=pause_windows
        ),
    )


def now_in_zone(tz_name: str | None) -> datetime:
    """Timezone-aware now. tz_name None → host local time."""
    if tz_name is None:
        return datetime.now().astimezone()
    return datetime.now(ZoneInfo(tz_name))


def valid_timezone(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
