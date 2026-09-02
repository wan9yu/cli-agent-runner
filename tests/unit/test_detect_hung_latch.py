from __future__ import annotations

from datetime import UTC, datetime

from agent_runner.monitor import detect_hung


def _ev(kind: str, rn: int, ts: str, phase=None):
    e = {"event": kind, "round_num": rn, "ts": ts}
    if phase is not None:
        e["phase"] = phase
    return e


def test_ancient_unclosed_round_does_not_latch_hung_after_later_rounds_complete() -> None:
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    events = [
        _ev("round_start", 1, "2026-08-01T00:00:00Z"),  # round_end lost (crash) — stale
        _ev("round_start", 2, "2026-08-30T11:59:50Z"),  # newest open, only 10s old
        _ev("round_end", 2, "2026-08-30T11:59:55Z"),
        _ev("round_start", 3, "2026-08-30T11:59:58Z"),  # newest open, 2s old
    ]
    # Old round 1 is weeks stale but must NOT alert; only round 3 is a live candidate.
    assert detect_hung(events, now=now, round_timeout_s=1800) is None


def test_newest_open_round_past_threshold_still_alerts() -> None:
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    events = [_ev("round_start", 7, "2026-08-30T10:00:00Z")]  # 2h old, threshold 45m
    a = detect_hung(events, now=now, round_timeout_s=1800)
    assert a is not None and a.context["round_num"] == 7


def test_crashed_round_with_no_newer_open_round_stops_latching_once_next_round_closes() -> None:
    """monitor.py's comment says the check is against the "highest-numbered
    round", but the pre-fix code took ``max(open_rounds)`` — the max of only the
    STILL-OPEN round numbers. Once round 2 starts AND closes normally, it drops
    out of ``open_rounds`` entirely, so ``max(open_rounds)`` reverts to the
    ancient crashed round 1 and alerts `hung` on it forever (advisory-only, but
    every peek from then on). The comment's actual intent — only the
    highest-numbered round OVERALL (open or closed) can be a live hang — means
    that once round 2 (higher than the crashed round 1) has closed, round 1's
    crash is superseded and must NOT alert."""
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
    events = [
        _ev("round_start", 1, "2026-08-01T00:00:00Z"),  # crashed weeks ago, no round_end
        _ev("round_start", 2, "2026-08-30T11:00:00Z"),
        _ev("round_end", 2, "2026-08-30T11:05:00Z"),  # round 2 completed normally
    ]
    assert detect_hung(events, now=now, round_timeout_s=1800) is None
