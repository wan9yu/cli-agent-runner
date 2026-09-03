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
    """Write events to monthly events file (truncates any existing content)."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    events_path = log_dir / f"events-{now.strftime('%Y-%m')}.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _append_events(log_dir: Path, events: list[dict]):
    """Append to the monthly events file — for tests that simulate a growing
    events stream (each ``transient_error_detected`` persisted before the
    supervisor's next read), unlike ``_write_events`` which truncates."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    events_path = log_dir / f"events-{now.strftime('%Y-%m')}.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a") as f:
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


def test_check_throttle_state_reads_ladder_extended_reset_not_raw(tmp_path):
    """The loop-top NON-skip gate (serve_cmd.py:382 -> _check_throttle_state) must
    read the SAME ladder-extended reset as _active_throttles (skip path /
    crash-loop excuse) and effective_throttle_view (peek) -- not the emitter's raw
    reset_at_epoch. Two consecutive api_timeout detections push the exponent to 1,
    extending the reset 30s (base) past the RAW one. Placing `now` past the raw
    reset but before the extended one reproduces the bug: pre-fix this returned
    None, so a non-default restart_delay_s (>=15s for api_timeout) sleeping past
    the raw reset between rounds would wave the next round through with NO
    back-off at all -- a flat loop, breaker disarmed (the agent-agnostic crash-loop
    excuse still sees the ladder via _active_throttles), while peek kept correctly
    reporting still-throttled."""
    from agent_runner._throttle import _active_throttles, _check_throttle_state

    raw_reset = 10_000  # api_timeout base=30s
    _write_events(
        tmp_path,
        [
            _detected(raw_reset, ts=_iso(raw_reset - 30), cls="api_timeout"),
            _detected(raw_reset, ts=_iso(raw_reset - 1), cls="api_timeout"),
        ],
    )
    clock = FakeClock(epoch=float(raw_reset + 5))  # past raw (+0), before extended (+30)
    state = _check_throttle_state(tmp_path, clock=clock)
    active = _active_throttles(tmp_path, clock=clock)
    assert state is not None  # still throttled per the ladder, not "cleared" per raw
    assert state.reset_at_epoch == raw_reset + 30
    assert state.reset_at_epoch == active["claude"].reset_at_epoch  # agrees with the skip map


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
    assert pending == [("deepseek", "rate_limit_account", 300)]  # (agent, cls, throttled_for_s)


def test_pending_recovered_empty_while_still_throttled(tmp_path):
    from agent_runner._throttle import pending_recovered

    _write_events(tmp_path, [_detected(int(time.time() + 3600))])
    assert pending_recovered(tmp_path) == []


def test_pending_recovered_empty_when_recovered_already_emitted(tmp_path):
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
    assert pending_recovered(tmp_path) == []


def test_pending_recovered_empty_with_no_events(tmp_path):
    from agent_runner._throttle import pending_recovered

    assert pending_recovered(tmp_path) == []


def test_pending_recovered_overlapping_one_clears_while_other_active(tmp_path):
    """Agent A cleared (reset past) while agent B still throttled (reset future):
    A is reported for its breadcrumb, B is not."""
    from agent_runner._throttle import pending_recovered

    now = 1_700_000_000
    _write_events(
        tmp_path,
        [
            _detected(now - 60, ts=_iso(now - 200), agent="claude"),  # cleared
            _detected(now + 3600, ts=_iso(now - 100), agent="gemini"),  # still throttled
        ],
    )
    pending = pending_recovered(tmp_path, clock=FakeClock(epoch=float(now)))
    assert pending == [("claude", "rate_limit_account", 200)]


def test_pending_recovered_holds_for_estimated_class_until_extended_reset(tmp_path):
    """Estimated-class ladder: once _backoff_exponent pushes the effective reset
    past the emitter's raw reset_at_epoch, pending_recovered must NOT report the
    agent cleared just because the RAW reset passed — it has to agree with
    _active_throttles, which gates on the exp-backoff-EXTENDED reset. Otherwise
    the skip loop emits a recovered breadcrumb while the ladder still holds,
    the agent drops out of _active_throttles early, and its phase retries at the
    flat raw cadence instead of the escalated one."""
    from agent_runner._throttle import _active_throttles, pending_recovered

    raw_reset = 10_000
    # Two detections for the same agent -> _backoff_exponent == 1 -> api_transient_5xx
    # (base=60s) extension = 60 * (2**1 - 1) = 60s -> extended reset = raw_reset + 60.
    _write_events(
        tmp_path,
        [
            _detected(raw_reset, ts=_iso(raw_reset - 120), agent="codex", cls="api_transient_5xx"),
            _detected(raw_reset, ts=_iso(raw_reset - 60), agent="codex", cls="api_transient_5xx"),
        ],
    )
    still_extended = FakeClock(epoch=float(raw_reset + 30))  # past raw, before extended
    assert "codex" in _active_throttles(tmp_path, clock=still_extended)
    assert pending_recovered(tmp_path, clock=still_extended) == []

    past_extended = FakeClock(epoch=float(raw_reset + 61))  # past the extended reset too
    assert "codex" not in _active_throttles(tmp_path, clock=past_extended)
    pending = pending_recovered(tmp_path, clock=past_extended)
    assert pending and pending[0][0] == "codex"


def test_active_throttles_keys_by_agent_multiple(tmp_path):
    from agent_runner._throttle import _active_throttles

    now = 1_700_000_000
    _write_events(
        tmp_path,
        [
            _detected(now + 3600, agent="claude", cls="rate_limit_account"),
            _detected(now + 1800, agent="gemini", cls="rate_limit_model"),
            _detected(now - 60, agent="codewhale"),  # reset passed → not active
        ],
    )
    active = _active_throttles(tmp_path, clock=FakeClock(epoch=float(now)))
    assert set(active) == {"claude", "gemini"}
    assert active["gemini"].reset_at_epoch == now + 1800


def test_active_throttles_latest_per_agent_recovered_clears(tmp_path):
    """A recovered after a detected for the same agent clears that agent."""
    from agent_runner._throttle import _active_throttles

    now = 1_700_000_000
    _write_events(
        tmp_path,
        [
            _detected(now + 3600, agent="claude"),
            {"event": "transient_error_recovered", "agent": "claude", "throttled_for_s": 10},
        ],
    )
    assert _active_throttles(tmp_path, clock=FakeClock(epoch=float(now))) == {}


def test_active_throttles_spans_month_boundary_merged_per_agent(tmp_path):
    """Agent A's detected in the OLD monthly file + agent B's in the NEW file → both
    active (per-agent scan must not early-exit at the first file with any transient)."""
    from agent_runner._throttle import _active_throttles

    now = 1_700_000_000
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "events-2026-04.jsonl").write_text(
        json.dumps(_detected(now + 3600, agent="claude")) + "\n"
    )
    (tmp_path / "events-2026-05.jsonl").write_text(
        json.dumps(_detected(now + 1800, agent="gemini")) + "\n"
    )
    active = _active_throttles(tmp_path, clock=FakeClock(epoch=float(now)))
    assert set(active) == {"claude", "gemini"}


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


def test_interruptible_sleep_terminates_when_sleep_is_noop():
    """Regression: a no-op sleep whose monotonic never advances (a test patching only
    time.sleep) must NOT busy-spin — the count-down loop terminates after
    ceil(total/chunk) slices instead of looping until a real deadline."""
    from agent_runner._throttle import _interruptible_sleep

    class _FrozenNoopClock:
        def __init__(self):
            self.calls = 0

        def epoch(self):
            return 0.0

        def monotonic(self):
            return 0.0  # never advances — the old deadline loop would spin forever

        def sleep(self, _s):
            self.calls += 1  # no-op: does not advance time

        def now_utc(self):
            raise NotImplementedError

        def now_in_zone(self, _tz):
            raise NotImplementedError

    clock = _FrozenNoopClock()
    interrupted = _interruptible_sleep(70.0, {"requested": False}, clock=clock, chunk_s=30)
    assert interrupted is False
    assert clock.calls == 3  # 30 + 30 + 10 → remaining 0; no infinite loop


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


def test_given_clock_jump_forward_during_capped_back_off_wakes_early(tmp_path):
    """RTC-less back-off: an on-host clock reading hours behind real time inflates
    the pre-computed sleep past the 8h magnitude cap (a Pi booting with no battery
    RTC, before NTP has synced). Once NTP corrects the clock MID-SLEEP, the back-off
    must wake as soon as the real wall-clock target passes rather than riding out
    the full pre-computed (stale-clock) duration on an already-expired throttle."""
    from agent_runner._throttle import _apply_back_off
    from agent_runner.api_types import TransientErrorState

    base = FakeClock(epoch=1_700_000_000.0)

    class _NTPJumpClock:
        """Clock adapter whose SECOND sleep() call also warps the wall clock far
        forward — simulating an NTP correction landing mid chunked sleep."""

        def __init__(self, inner):
            self._c = inner
            self.slept: list[float] = []
            self._sleep_calls = 0

        def epoch(self):
            return self._c.epoch()

        def monotonic(self):
            return self._c.monotonic()

        def sleep(self, seconds):
            self.slept.append(seconds)
            self._c.sleep(seconds)
            self._sleep_calls += 1
            if self._sleep_calls == 2:
                self._c.warp_epoch(60_000.0)  # NTP catches the stale clock up

        def now_utc(self):
            return self._c.now_utc()

        def now_in_zone(self, tz_name):
            return self._c.now_in_zone(tz_name)

    clock = _NTPJumpClock(base)
    # 50_000s out from the clock's (stale) view — past the 8h cap, so this hits the
    # magnitude-cap branch, but the target itself is a real, reachable wall-clock time.
    throttle = TransientErrorState(
        reset_at_epoch=int(base.epoch()) + 50_000,
        classification="rate_limit_account",
        agent="claude",
        since_round=1,
    )
    with patch("agent_runner._emit.emit_transient_error_backoff_capped"):
        with patch("agent_runner._emit.emit_transient_error_recovered") as mock_recovered:
            interrupted = _apply_back_off(
                tmp_path, throttle, stop={"requested": False}, clock=clock, chunk_s=30
            )
    assert interrupted is False
    # Woke 2 chunks in, right after the jump — nowhere near the 960 chunks
    # (28800 / 30) the un-fixed 8h cap would otherwise require.
    assert clock.slept == [30.0, 30.0]
    mock_recovered.assert_called_once()


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
    """Second consecutive failure: exponent (from 2 persisted detections) is 1 →
    multiplier = 2^1 = 2×. The events-derived ladder counts the SAME agent's
    ``transient_error_detected`` history, not a repeated in-process call."""
    from agent_runner import _throttle

    now = 1_700_000_000.0
    _write_events(
        tmp_path,
        [
            _detected(int(now) + 60, cls="rate_limit_model"),
            _detected(int(now) + 60, cls="rate_limit_model"),
        ],
    )
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
    """6 persisted detections → exponent=5 → multiplier=32× (2^5), capped at 1800s.
    The exp_cap (5) plateaus the multiplier beyond this."""
    from agent_runner import _throttle

    now = 1_700_000_000.0
    _write_events(tmp_path, [_detected(int(now) + 60, cls="rate_limit_model") for _ in range(6)])
    # 6th detection already persisted: n=5 → multiplier=32 → duration=60*32=1920s but capped at 1800
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
    """api_timeout has 30s base; 6 persisted detections → exponent=5 → multiplier=32 →
    30*32=960s (under cap)."""
    from agent_runner import _throttle

    now = 1_700_000_000.0
    _write_events(tmp_path, [_detected(int(now) + 30, cls="api_timeout") for _ in range(6)])
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


def test_compute_adjusted_reset_at_rate_limit_account_exempt(tmp_path):
    """Server-authoritative rate_limit_account: counter never increments,
    returned reset is the original (resetsAt from server), no event fires."""
    import json

    from agent_runner import _throttle

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
    """When multiplier > 1, emit transient_error_backoff_capped with all new fields.

    Mirrors the real call sequence: the plugin already persisted a
    ``transient_error_detected`` for each occurrence before the supervisor calls
    ``compute_adjusted_reset_at`` — the exponent is read from that stream, not
    incremented by the call itself."""
    import json

    from agent_runner import _throttle

    now = int(time.time())
    # First detection persisted: exponent=0 → multiplier=1, no event
    _append_events(tmp_path, [_detected(now + 60, cls="rate_limit_model")])
    _throttle.compute_adjusted_reset_at(
        classification="rate_limit_model",
        original_reset_at_epoch=now + 60,
        agent="claude",
        log_dir=tmp_path,
    )
    # Second detection persisted: exponent=1 → multiplier=2, event should fire
    _append_events(tmp_path, [_detected(now + 60, cls="rate_limit_model")])
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


def test_elapsed_s_clamps_backward_clock_step_to_zero() -> None:
    from agent_runner._throttle import _elapsed_s

    clk = FakeClock(epoch=1000.0)
    assert _elapsed_s(900.0, clock=clk) == 100
    clk.warp_epoch(-500.0)  # NTP step backward: now < since_epoch
    assert _elapsed_s(900.0, clock=clk) == 0  # never negative


def test_apply_back_off_emits_raw_detector_reset_not_ladder_extended(tmp_path):
    """_apply_back_off hands compute_adjusted_reset_at the RAW detector reset for
    the transient_error_backoff_capped event's original_reset_at_epoch field --
    not throttle.reset_at_epoch, which _check_throttle_state already ladder-
    extends (see test_check_throttle_state_reads_ladder_extended_reset_not_raw).
    0.3's structured-event consumers would otherwise inherit the wrong field."""
    from agent_runner._throttle import _apply_back_off, _check_throttle_state

    clock = FakeClock(epoch=1_700_000_000.0)
    raw_reset = int(clock.epoch()) + 100  # api_timeout base=30s
    _write_events(
        tmp_path,
        [
            _detected(raw_reset, ts=_iso(raw_reset - 60), cls="api_timeout"),
            _detected(raw_reset, ts=_iso(raw_reset - 30), cls="api_timeout"),
        ],
    )
    throttle = _check_throttle_state(tmp_path, clock=clock)
    assert throttle is not None
    assert throttle.reset_at_epoch == raw_reset + 30  # ladder-extended (exponent=1)

    with patch("agent_runner._emit.emit_transient_error_recovered"):
        interrupted = _apply_back_off(tmp_path, throttle, stop={"requested": True}, clock=clock)
    assert interrupted is True

    capped = [
        json.loads(line)
        for f in sorted(tmp_path.glob("events-*.jsonl"))
        for line in f.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    capped = [e for e in capped if e.get("event") == "transient_error_backoff_capped"]
    assert len(capped) == 1
    assert capped[0]["original_reset_at_epoch"] == raw_reset  # RAW, not raw_reset + 30


def test_apply_back_off_recovered_throttled_for_s_matches_sleep(tmp_path) -> None:
    from agent_runner._throttle import _apply_back_off
    from agent_runner.api_types import TransientErrorState

    clk = FakeClock(epoch=1000.0)
    throttle = TransientErrorState(
        reset_at_epoch=1000 + 120,
        classification="rate_limit_account",  # server-authoritative → verbatim reset
        agent="claude",
        since_round=1,
        phase="",
    )
    stop = {"requested": False}
    assert _apply_back_off(tmp_path, throttle, stop=stop, clock=clk) is False
    rec = [
        json.loads(line)
        for f in tmp_path.glob("events-*.jsonl")
        for line in f.read_text().splitlines()
        if json.loads(line)["event"] == "transient_error_recovered"
    ]
    assert rec and rec[0]["throttled_for_s"] >= 120  # slept ~120s + jitter, clamped >= 0
