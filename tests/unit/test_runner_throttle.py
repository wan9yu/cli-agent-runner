"""Unit tests for supervisor back-off logic in runner.py."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from tests._clock import FakeClock


def _iso(epoch: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC).isoformat()


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
    assert state.phase == ""  # no phase recorded → "" (back-compat)


def test_check_throttle_state_carries_phase(tmp_path):
    from agent_runner._throttle import _check_throttle_state

    _write_events(
        tmp_path,
        [
            {
                "event": "transient_error_detected",
                "ts": "2026-05-16T00:00:00Z",
                "agent": "deepseek-cli",
                "reset_at_epoch": int(time.time() + 3600),
                "classification": "rate_limit_model",
                "round_num": 7,
                "phase": "deepseek",
            }
        ],
    )
    state = _check_throttle_state(tmp_path)
    assert state is not None and state.phase == "deepseek"


def _detected(reset_at, *, ts="2026-05-16T00:00:00Z", agent="claude", cls="rate_limit_account"):
    return {
        "event": "transient_error_detected",
        "ts": ts,
        "agent": agent,
        "reset_at_epoch": reset_at,
        "classification": cls,
        "round_num": 42,
    }


def test_pending_recovered_fires_when_throttle_cleared_without_breadcrumb(tmp_path):
    from agent_runner._throttle import pending_recovered

    now = 1_700_000_000
    _write_events(tmp_path, [_detected(now - 60, ts=_iso(now - 300), agent="deepseek")])
    pending = pending_recovered(tmp_path, clock=FakeClock(epoch=float(now)))
    assert pending is not None
    classification, agent, throttled_for_s = pending
    assert agent == "deepseek"
    assert classification == "rate_limit_account"
    assert throttled_for_s == 300  # exact: now - detected ts, injected clock


def test_pending_recovered_none_while_still_throttled(tmp_path):
    from agent_runner._throttle import pending_recovered

    _write_events(tmp_path, [_detected(int(time.time() + 3600))])
    assert pending_recovered(tmp_path) is None


def test_pending_recovered_none_when_recovered_already_emitted(tmp_path):
    """Dedup guard: the back-off path already left a recovered → stay quiet."""
    from agent_runner._throttle import pending_recovered

    _write_events(
        tmp_path,
        [
            _detected(int(time.time() - 60)),
            {
                "event": "transient_error_recovered",
                "ts": "2026-05-16T00:01:00Z",
                "agent": "claude",
                "throttled_for_s": 60,
                "classification": "rate_limit_account",
            },
        ],
    )
    assert pending_recovered(tmp_path) is None


def test_pending_recovered_none_with_no_events(tmp_path):
    from agent_runner._throttle import pending_recovered

    assert pending_recovered(tmp_path) is None


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


def test_interruptible_sleep_chunks_without_overshoot_and_returns_false():
    """Uninterrupted: sleeps in <= chunk_s slices summing to total, no overshoot."""
    from agent_runner._throttle import _interruptible_sleep

    clock = FakeClock(epoch=1_700_000_000.0)
    interrupted = _interruptible_sleep(70.0, {"requested": False}, clock=clock, chunk_s=30)
    assert interrupted is False
    assert clock.slept == [30.0, 30.0, 10.0]  # final slice capped at the remainder


def test_interruptible_sleep_short_delay_single_slice():
    """A delay below one chunk sleeps exactly that delay (no chunk-sized overshoot)."""
    from agent_runner._throttle import _interruptible_sleep

    clock = FakeClock(epoch=1_700_000_000.0)
    _interruptible_sleep(5.0, {"requested": False}, clock=clock, chunk_s=30)
    assert clock.slept == [5.0]


def test_interruptible_sleep_returns_true_when_stop_preset():
    from agent_runner._throttle import _interruptible_sleep

    clock = FakeClock(epoch=1_700_000_000.0)
    assert _interruptible_sleep(100.0, {"requested": True}, clock=clock) is True
    assert clock.slept == []


def test_given_sleep_exceeds_cap_when_back_off_then_capped_and_emits_warning(tmp_path):
    """When reset_at_epoch implies sleep > 8h, cap and emit transient_error_backoff_capped."""
    from agent_runner._throttle import _apply_back_off
    from agent_runner.api_types import TransientErrorState

    clock = FakeClock(epoch=1_700_000_000.0)
    far_future = int(clock.epoch() + 86400)  # 24h out
    throttle = TransientErrorState(
        reset_at_epoch=far_future,
        classification="rate_limit_account",
        agent="claude",
        since_round=42,
    )
    with patch("agent_runner._emit.emit_transient_error_backoff_capped") as mock_new_capped:
        with patch("agent_runner._emit.emit_transient_error_recovered") as mock_new_recovered:
            # chunk_s == cap so the capped sleep is one slice (not 960 × 30s chunks).
            interrupted = _apply_back_off(
                tmp_path, throttle, stop={"requested": False}, clock=clock, chunk_s=28800
            )
    assert interrupted is False
    # sleep should be capped — FakeClock records the requested sleep in .slept
    assert clock.slept
    assert clock.slept[0] <= 28800 + 30  # 8h cap + max jitter
    mock_new_capped.assert_called_once()
    mock_new_recovered.assert_called_once()


def test_given_stop_set_mid_back_off_then_returns_true_and_no_recovered(tmp_path):
    """A SIGTERM during a multi-hour back-off must land within one chunk and leave NO
    recovered breadcrumb — the throttle is still active, so recovering would poison it."""
    from agent_runner._throttle import _apply_back_off
    from agent_runner.api_types import TransientErrorState

    base = FakeClock(epoch=1_700_000_000.0)
    stop = {"requested": False}

    class _StopOnFirstSleep:
        """Clock adapter that flips ``stop`` the moment the loop sleeps its first chunk,
        simulating a SIGTERM arriving mid-window."""

        def __init__(self, inner):
            self._c = inner
            self.slept: list[float] = []

        def epoch(self):
            return self._c.epoch()

        def monotonic(self):
            return self._c.monotonic()

        def sleep(self, seconds):
            self.slept.append(seconds)
            self._c.sleep(seconds)
            stop["requested"] = True

        def now_utc(self):
            return self._c.now_utc()

        def now_in_zone(self, tz_name):
            return self._c.now_in_zone(tz_name)

    clock = _StopOnFirstSleep(base)
    throttle = TransientErrorState(
        reset_at_epoch=int(base.epoch() + 3600),  # 1h out
        classification="rate_limit_account",
        agent="claude",
        since_round=1,
    )
    with patch("agent_runner._emit.emit_transient_error_recovered") as mock_recovered:
        interrupted = _apply_back_off(tmp_path, throttle, stop=stop, clock=clock, chunk_s=30)
    assert interrupted is True
    assert clock.slept == [30.0]  # exactly one 30s chunk, not the full hour
    mock_recovered.assert_not_called()


def test_given_stop_preset_when_back_off_then_returns_true_without_sleeping(tmp_path):
    """stop already requested at entry → return True before any sleep or recovered emit."""
    from agent_runner._throttle import _apply_back_off
    from agent_runner.api_types import TransientErrorState

    clock = FakeClock(epoch=1_700_000_000.0)
    throttle = TransientErrorState(
        reset_at_epoch=int(clock.epoch() + 3600),
        classification="rate_limit_account",
        agent="claude",
        since_round=1,
    )
    with patch("agent_runner._emit.emit_transient_error_recovered") as mock_recovered:
        interrupted = _apply_back_off(tmp_path, throttle, stop={"requested": True}, clock=clock)
    assert interrupted is True
    assert clock.slept == []
    mock_recovered.assert_not_called()


def test_compute_adjusted_reset_at_first_failure_no_multiplier(tmp_path):
    """First failure of a bucket: multiplier = 2^0 = 1×; applied = original."""
    from agent_runner import _throttle

    _throttle.reset_counters()  # ensure clean state
    now = 1_700_000_000.0  # injected clock → exact, no time.time() drift
    applied, count, capped = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=int(now) + 60,
        agent="claude",
        log_dir=tmp_path,
        clock=FakeClock(epoch=now),
    )
    # multiplier = 1 → applied_duration = base (60s) → applied_reset = now + 60
    assert applied == int(now) + 60
    assert count == 1
    assert capped is False


def test_compute_adjusted_reset_at_second_failure_doubles(tmp_path):
    """Second consecutive failure: counter is 1 going in → multiplier = 2^1 = 2×."""
    from agent_runner import _throttle

    _throttle.reset_counters()
    now = 1_700_000_000.0
    # First call increments counter to 1
    _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=int(now) + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    # Second call: n=1 → multiplier=2 → applied_duration=120s
    applied, count, capped = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=int(now) + 60,
        agent="claude",
        log_dir=tmp_path,
        clock=FakeClock(epoch=now),
    )
    assert applied == int(now) + 120  # exact via injected clock
    assert count == 2
    assert capped is False


def test_compute_adjusted_reset_at_sixth_plateaus_at_32x(tmp_path):
    """After 5 prior failures (counter=5), 6th call uses multiplier=32× (2^5).
    7th call should plateau at 32× (exp_cap=5 means n is clamped)."""
    from agent_runner import _throttle

    _throttle.reset_counters()
    now = 1_700_000_000.0
    # Pump counter to 5 (5 calls)
    for _ in range(5):
        _throttle.compute_adjusted_reset_at(
            classification="rate_limit_model",
            original_reset_at_epoch=int(now) + 60,
            agent="claude",
            log_dir=tmp_path,
        )
    # 6th call: n=5 → multiplier=32 → duration=60*32=1920s but capped at 1800
    applied_6, count_6, capped_6 = _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=int(now) + 60,
        agent="claude",
        log_dir=tmp_path,
        clock=FakeClock(epoch=now),
    )
    assert count_6 == 6
    assert capped_6 is True  # 60*32=1920 > 1800
    assert applied_6 == int(now) + 1800  # capped, exact


def test_compute_adjusted_reset_at_api_timeout_30s_base(tmp_path):
    """api_timeout has 30s base; multiplier=2 → 60s; multiplier=32 → 960s (under cap)."""
    from agent_runner import _throttle

    _throttle.reset_counters()
    now = 1_700_000_000.0
    # Force counter=5 → multiplier=32 → 30*32=960s, well under 1800 cap
    for _ in range(5):
        _throttle.compute_adjusted_reset_at(
            classification="api_timeout",
            original_reset_at_epoch=int(now) + 30,
            agent="claude",
            log_dir=tmp_path,
        )
    applied, count, capped = _throttle.compute_adjusted_reset_at(
        classification="api_timeout",
        original_reset_at_epoch=int(now) + 30,
        agent="claude",
        log_dir=tmp_path,
        clock=FakeClock(epoch=now),
    )
    assert count == 6
    assert capped is False  # 30*32=960 < 1800
    assert applied == int(now) + 960  # exact


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
