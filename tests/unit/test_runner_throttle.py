"""Unit tests for supervisor back-off logic in runner.py."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch


def _write_events(log_dir: Path, events: list[dict]):
    """Write events to monthly events file."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    events_path = log_dir / f"events-{now.strftime('%Y-%m')}.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_given_rejected_with_reset_in_future_when_check_then_returns_throttle_state(tmp_path):
    from agent_runner._throttle import _check_throttle_state

    future = int(time.time() + 3600)
    _write_events(
        tmp_path,
        [
            {
                "event": "transient_error_detected",
                "ts": "2026-05-16T00:00:00Z",
                "agent": "claude",
                "reset_at_epoch": future,
                "classification": "rate_limit_account",
                "round_num": 42,
            }
        ],
    )
    state = _check_throttle_state(tmp_path)
    assert state is not None
    assert state.reset_at_epoch == future
    assert state.classification == "rate_limit_account"
    assert state.agent == "claude"
    assert state.since_round == 42


def test_given_rejected_followed_by_recovered_when_check_then_returns_none(tmp_path):
    from agent_runner._throttle import _check_throttle_state

    future = int(time.time() + 3600)
    _write_events(
        tmp_path,
        [
            {
                "event": "transient_error_detected",
                "ts": "2026-05-16T00:00:00Z",
                "agent": "claude",
                "reset_at_epoch": future,
                "classification": "rate_limit_account",
                "round_num": 42,
            },
            {
                "event": "transient_error_recovered",
                "ts": "2026-05-16T00:01:00Z",
                "agent": "claude",
                "throttled_for_s": 60,
                "classification": "rate_limit_account",
            },
        ],
    )
    state = _check_throttle_state(tmp_path)
    assert state is None


def test_given_rejected_with_reset_in_past_when_check_then_returns_none(tmp_path):
    from agent_runner._throttle import _check_throttle_state

    past = int(time.time() - 3600)
    _write_events(
        tmp_path,
        [
            {
                "event": "transient_error_detected",
                "ts": "2026-05-16T00:00:00Z",
                "agent": "claude",
                "reset_at_epoch": past,
                "classification": "rate_limit_account",
                "round_num": 42,
            }
        ],
    )
    state = _check_throttle_state(tmp_path)
    assert state is None


def test_given_no_events_when_check_then_returns_none(tmp_path):
    from agent_runner._throttle import _check_throttle_state

    state = _check_throttle_state(tmp_path)
    assert state is None


def test_given_sleep_exceeds_cap_when_back_off_then_capped_and_emits_warning(tmp_path):
    """When reset_at_epoch implies sleep > 8h, cap and emit transient_error_backoff_capped."""
    from agent_runner.api_types import TransientErrorState
    from agent_runner.runner import _apply_back_off

    far_future = int(time.time() + 86400)  # 24h out
    throttle = TransientErrorState(
        reset_at_epoch=far_future,
        classification="rate_limit_account",
        agent="claude",
        since_round=42,
    )
    with patch("agent_runner.runner.time.sleep") as mock_sleep:
        with patch("agent_runner.api.emit_transient_error_backoff_capped") as mock_new_capped:
            with patch("agent_runner.api.emit_transient_error_recovered") as mock_new_recovered:
                _apply_back_off(tmp_path, throttle)
    # sleep should be capped
    assert mock_sleep.called
    sleep_arg = mock_sleep.call_args[0][0]
    assert sleep_arg <= 28800 + 30  # 8h cap + max jitter
    mock_new_capped.assert_called_once()
    mock_new_recovered.assert_called_once()


def test_compute_adjusted_reset_at_first_failure_no_multiplier(tmp_path):
    """First failure of a bucket: multiplier = 2^0 = 1×; applied = original."""
    from agent_runner import _throttle

    _throttle.reset_counters()  # ensure clean state
    original_reset = int(time.time()) + 60
    applied, count, capped = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=original_reset,
        agent="claude",
        log_dir=tmp_path,
    )
    # multiplier = 1 → applied_duration = base (60s) → applied_reset ≈ now + 60
    assert abs(applied - original_reset) <= 1  # within 1s tolerance
    assert count == 1
    assert capped is False


def test_compute_adjusted_reset_at_second_failure_doubles(tmp_path):
    """Second consecutive failure: counter is 1 going in → multiplier = 2^1 = 2×."""
    from agent_runner import _throttle

    _throttle.reset_counters()
    now = int(time.time())
    # First call increments counter to 1
    _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=now + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    # Second call: n=1 → multiplier=2 → applied_duration=120s
    applied, count, capped = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=now + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    expected_applied = int(time.time()) + 120
    assert abs(applied - expected_applied) <= 2  # 2s tolerance for time.time drift
    assert count == 2
    assert capped is False


def test_compute_adjusted_reset_at_sixth_plateaus_at_32x(tmp_path):
    """After 5 prior failures (counter=5), 6th call uses multiplier=32× (2^5).
    7th call should plateau at 32× (exp_cap=5 means n is clamped)."""
    from agent_runner import _throttle

    _throttle.reset_counters()
    now = int(time.time())
    # Pump counter to 5 (5 calls)
    for _ in range(5):
        _throttle.compute_adjusted_reset_at(
            classification="rate_limit_model",
            original_reset_at_epoch=now + 60,
            agent="claude",
            log_dir=tmp_path,
        )
    # 6th call: n=5 → multiplier=32 → duration=60*32=1920s but capped at 1800
    applied_6, count_6, capped_6 = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=now + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    assert count_6 == 6
    assert capped_6 is True  # 60*32=1920 > 1800
    # applied ≈ now + 1800
    assert abs(applied_6 - (int(time.time()) + 1800)) <= 2


def test_compute_adjusted_reset_at_api_timeout_30s_base(tmp_path):
    """api_timeout has 30s base; multiplier=2 → 60s; multiplier=32 → 960s (under cap)."""
    from agent_runner import _throttle

    _throttle.reset_counters()
    now = int(time.time())
    # Force counter=5 → multiplier=32 → 30*32=960s, well under 1800 cap
    for _ in range(5):
        _throttle.compute_adjusted_reset_at(
            classification="api_timeout",
            original_reset_at_epoch=now + 30,
            agent="claude",
            log_dir=tmp_path,
        )
    applied, count, capped = _throttle.compute_adjusted_reset_at(
        classification="api_timeout",
        original_reset_at_epoch=now + 30,
        agent="claude",
        log_dir=tmp_path,
    )
    assert count == 6
    assert capped is False  # 30*32=960 < 1800
    assert abs(applied - (int(time.time()) + 960)) <= 2


def test_reset_counters_clears_all_buckets(tmp_path):
    """reset_counters() after compute_adjusted_reset_at calls returns to fresh state."""
    from agent_runner import _throttle

    _throttle.reset_counters()
    now = int(time.time())
    _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=now + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    _throttle.compute_adjusted_reset_at(
        classification="api_timeout",
        original_reset_at_epoch=now + 30,
        agent="claude",
        log_dir=tmp_path,
    )
    _throttle.reset_counters()
    # Next call to either bucket should start at counter=0 again
    applied, count, _ = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=now + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    assert count == 1  # fresh start


def test_compute_adjusted_reset_at_rate_limit_account_exempt(tmp_path):
    """Server-authoritative rate_limit_account: counter never increments,
    returned reset is the original (resetsAt from server), no event fires."""
    import json

    from agent_runner import _throttle

    _throttle.reset_counters()
    server_reset = int(time.time()) + 18000  # 5h from now (resetsAt from Anthropic)
    applied, count, capped = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_account",
        original_reset_at_epoch=server_reset,
        agent="claude",
        log_dir=tmp_path,
    )
    assert applied == server_reset  # exactly server's value, no multiplier
    assert count == 0  # not incremented
    assert capped is False
    # Repeat call: still no increment
    applied2, count2, _ = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_account",
        original_reset_at_epoch=server_reset,
        agent="claude",
        log_dir=tmp_path,
    )
    assert count2 == 0  # still not incremented
    # Verify no transient_error_backoff_capped event was emitted for rate_limit_account
    events_files = sorted(tmp_path.glob("events-*.jsonl"))
    capped_events = []
    for f in events_files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "transient_error_backoff_capped":
                capped_events.append(ev)
    assert capped_events == []  # no events fired for server-authoritative bucket


def test_compute_adjusted_reset_at_emits_backoff_capped_event_on_adjustment(tmp_path):
    """When multiplier > 1, emit transient_error_backoff_capped with all new fields."""
    import json

    from agent_runner import _throttle

    _throttle.reset_counters()
    now = int(time.time())
    # First call: multiplier=1, no event
    _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=now + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    # Second call: multiplier=2, event should fire
    applied, count, capped = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=now + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    events_files = sorted(tmp_path.glob("events-*.jsonl"))
    capped_events = []
    for f in events_files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "transient_error_backoff_capped":
                capped_events.append(ev)
    assert len(capped_events) == 1  # only the 2nd call (multiplier=2) emitted
    ev = capped_events[0]
    assert ev["classification"] == "rate_limit_model"
    assert ev["agent"] == "claude"
    assert ev["consecutive_count"] == 2
    assert ev["capped_by_absolute_max"] is False
    assert ev["original_reset_at_epoch"] == now + 60
    assert ev["applied_reset_at_epoch"] == applied
