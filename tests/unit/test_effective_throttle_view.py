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


def test_scalar_reconciles_to_active_escalated_reset(tmp_path: Path) -> None:
    """The scalar (`_check_throttle_state`, unextended) must be swapped for the
    active map's ESCALATED entry when the two refer to the same agent — not just in
    the None-fallback case above. Otherwise api.peek / http_progress display the raw
    (shorter) reset while the skip loop gates on the extended one: an operator sees
    the stated time pass with the agent still throttled, with no explanation."""
    clock = FakeClock(epoch=1_000)
    raw_reset = 10_000
    # 3 consecutive detections, no success between → exponent=2, escalated reset > raw.
    evs = [
        {
            "event": "transient_error_detected",
            "agent": "claude",
            "classification": "api_transient_5xx",
            "reset_at_epoch": raw_reset,
            "round_num": i,
        }
        for i in range(1, 4)
    ]
    (tmp_path / "events-2026-08.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n")
    throttle, active = effective_throttle_view(tmp_path, clock=clock)
    assert throttle is not None
    assert throttle.reset_at_epoch > raw_reset  # not the verbatim emitter value
    assert throttle.reset_at_epoch == active["claude"].reset_at_epoch  # agrees with the map
    assert throttle.classification == active["claude"].classification
