from __future__ import annotations

from agent_runner.monitor import detect_orphan_chain


def _ev(kind, rn):
    return {"event": kind, "round_num": rn}


def test_three_consecutive_orphans_alerts() -> None:
    events = []
    for rn in (1, 2, 3):
        events += [_ev("orphan_stashed", rn), _ev("round_end", rn)]
    a = detect_orphan_chain(events, threshold=3)
    assert a is not None and a.context["streak"] == 3 and a.context["last_round"] == 3


def test_clean_round_breaks_the_streak() -> None:
    events = [
        _ev("orphan_stashed", 1),
        _ev("round_end", 1),
        _ev("round_end", 2),  # no orphan -> reset
        _ev("orphan_stashed", 3),
        _ev("round_end", 3),
    ]
    assert detect_orphan_chain(events, threshold=3) is None
