"""A poisoned reset_at_epoch (huge finite value, or negative) must clamp to the
default rather than flow through to a datetime.fromtimestamp call or pin a
throttle 'active' forever — see agent_runner._throttle._coerce_epoch_int."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

from agent_runner import _throttle
from tests._clock import FakeClock


def test_coerce_epoch_int_should_clamp_to_default_when_far_future():
    now = 1_700_000_000.0

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # a poisoned reset 100 years out → clamped to the default, not passed through
        assert _throttle._coerce_epoch_int(now + 100 * 366 * 86400, 0, now_epoch=now) == 0


def test_coerce_epoch_int_should_clamp_to_default_when_negative():
    now = 1_700_000_000.0

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert _throttle._coerce_epoch_int(-5, 0, now_epoch=now) == 0


def test_coerce_epoch_int_should_pass_through_when_within_valid_range():
    now = 1_700_000_000.0

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert _throttle._coerce_epoch_int(now + 3600, 0, now_epoch=now) == int(now + 3600)


def test_coerce_epoch_int_should_clamp_to_default_when_digit_string_is_huge():
    """A 20-digit string (e.g. a plugin that serialized a corrupted counter into the
    epoch field) coerces to an int fine, then must still fail the range check."""
    now = 1_700_000_000.0

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert _throttle._coerce_epoch_int("99999999999999999999", 0, now_epoch=now) == 0


def test_active_throttles_should_exclude_agent_when_reset_epoch_poisoned(tmp_path: Path) -> None:
    """End-to-end (real events file, real clock): a huge finite reset_at_epoch must
    not raise, and must not leave the skip path parked on an unreachable future.
    _active_throttles degrades the poisoned entry to the default (0, i.e. already
    reset) rather than an uncapped wake, so the agent is treated as NOT throttled —
    the skip path can select it again immediately instead of waiting on a wake_epoch
    ~1e20 seconds out."""
    clock = FakeClock(epoch=1_700_000_000.0)
    evs = [
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": 10**20,
            "classification": "rate_limit_account",
            "round_num": 1,
        },
    ]
    (tmp_path / "events-2026-05.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        active = _throttle._active_throttles(tmp_path, clock=clock)

    # No agent parked on the poisoned epoch — the skip path's wake_epoch (min of
    # these) has nothing to park on.
    assert "claude" not in active


def test_check_throttle_state_should_return_none_when_reset_epoch_poisoned(tmp_path: Path) -> None:
    clock = FakeClock(epoch=1_700_000_000.0)
    evs = [
        {
            "event": "transient_error_detected",
            "ts": "2026-05-16T00:00:00Z",
            "agent": "claude",
            "reset_at_epoch": 10**20,
            "classification": "rate_limit_account",
            "round_num": 1,
        },
    ]
    (tmp_path / "events-2026-05.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        state = _throttle._check_throttle_state(tmp_path, clock=clock)

    assert state is None  # degraded to "not throttled", not a crash
