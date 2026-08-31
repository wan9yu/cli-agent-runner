from __future__ import annotations

import json
from pathlib import Path

from agent_runner import _throttle
from tests._clock import FakeClock


def _write(log_dir: Path, events: list[dict]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "events-2026-08.jsonl").open("a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _detected(agent: str, reset_at: int) -> dict:
    return {
        "event": "transient_error_detected",
        "agent": agent,
        "classification": "api_transient_5xx",
        "reset_at_epoch": reset_at,
        "round_num": 1,
    }


def _success(agent: str) -> dict:
    return {"event": "agent_usage_recorded", "agent": agent, "success": True}


def test_first_detection_has_exponent_zero(tmp_path: Path) -> None:
    _write(tmp_path, [_detected("claude", 2_000)])
    assert _throttle._backoff_exponent(tmp_path, "claude") == 0


def test_consecutive_detections_grow_exponent(tmp_path: Path) -> None:
    _write(
        tmp_path,
        [_detected("claude", 2_000), _detected("claude", 2_100), _detected("claude", 2_200)],
    )
    assert _throttle._backoff_exponent(tmp_path, "claude") == 2


def test_success_resets_but_recovered_does_not(tmp_path: Path) -> None:
    # recovered fires every skip cycle — it must NOT reset the ladder.
    _write(
        tmp_path,
        [
            _detected("claude", 2_000),
            {"event": "transient_error_recovered", "agent": "claude"},
            _detected("claude", 2_100),
        ],
    )
    assert _throttle._backoff_exponent(tmp_path, "claude") == 1  # not pinned to 0
    _write(tmp_path, [_success("claude"), _detected("claude", 2_300)])
    assert _throttle._backoff_exponent(tmp_path, "claude") == 0  # success reset it


def test_skip_view_reset_grows_across_rounds(tmp_path: Path) -> None:
    clock = FakeClock(epoch=1_000)
    # 5xx base=... anchored to the emitter's reset (far future so it stays active).
    _write(
        tmp_path,
        [_detected("claude", 10_000), _detected("claude", 10_000), _detected("claude", 10_000)],
    )
    st = _throttle._active_throttles(tmp_path, clock=clock)["claude"]
    # exponent=2 → reset pushed past the raw 10_000 emitter estimate.
    assert st.reset_at_epoch > 10_000


def test_skip_view_is_restart_idempotent(tmp_path: Path) -> None:
    clock = FakeClock(epoch=1_000)
    _write(tmp_path, [_detected("claude", 10_000), _detected("claude", 10_000)])
    a = _throttle._active_throttles(tmp_path, clock=clock)["claude"].reset_at_epoch
    b = _throttle._active_throttles(tmp_path, clock=clock)["claude"].reset_at_epoch
    assert a == b  # anchored to reset_at, not now → no double-apply on re-scan/restart


def test_rate_limit_account_reset_is_verbatim(tmp_path: Path) -> None:
    clock = FakeClock(epoch=1_000)
    _write(
        tmp_path,
        [
            {
                "event": "transient_error_detected",
                "agent": "claude",
                "classification": "rate_limit_account",
                "reset_at_epoch": 10_000,
                "round_num": 1,
            }
        ]
        * 3,
    )
    st = _throttle._active_throttles(tmp_path, clock=clock)["claude"]
    assert st.reset_at_epoch == 10_000  # server-authoritative — never extended
