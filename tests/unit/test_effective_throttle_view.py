from __future__ import annotations

import json
from pathlib import Path

from agent_runner._throttle import effective_throttle_view
from tests._clock import FakeClock


def test_scalar_falls_back_to_last_clearing_active_agent(tmp_path: Path) -> None:
    clock = FakeClock(epoch=1_000_000.0)
    now = int(clock.epoch())
    # Agent A still throttled; agent B recovered LAST (so global-latest scalar is None).
    evs = [
        {
            "event": "transient_error_detected",
            "ts": "2026-08-30T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": now + 500,
            "classification": "rate_limit_account",
            "round_num": 1,
        },
        {
            "event": "transient_error_detected",
            "ts": "2026-08-30T00:00:01Z",
            "agent": "gemini",
            "reset_at_epoch": now + 100,
            "classification": "rate_limit_model",
            "round_num": 2,
        },
        {"event": "transient_error_recovered", "ts": "2026-08-30T00:00:02Z", "agent": "gemini"},
    ]
    (tmp_path / "events-2026-08.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n")
    scalar, active = effective_throttle_view(tmp_path, clock=clock)
    assert set(active) == {"claude"}
    assert scalar is not None and scalar.agent == "claude"  # not dropped to None


def test_no_throttle_returns_none_and_empty(tmp_path: Path) -> None:
    (tmp_path / "events-2026-08.jsonl").write_text("")
    assert effective_throttle_view(tmp_path, clock=FakeClock()) == (None, {})
