import pytest

from agent_runner import _round_outcome, events
from tests._clock import FakeClock


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch):
    # events.emit's ts is real-clock millisecond-resolution -- three calls back
    # to back in-process land in the SAME millisecond, which would tie the
    # `newest_usage_ts < newest_substrate_before_ts` comparison below. A
    # FakeClock advanced between emits gives each event its own strictly
    # increasing timestamp instead (project convention: all time via clock).
    clock = FakeClock()
    monkeypatch.setattr(events, "SYSTEM_CLOCK", clock)
    return clock


def _emit(log_dir, kind, clock, **f):
    events.emit(log_dir, kind, **f)
    clock.advance(1)


def test_kimi_round_should_not_be_no_progress_when_usageless_after_pi_usage(tmp_path, _fake_clock):
    """Mixed [phases]: pi emitted usage; kimi (never emits usage BY DESIGN) then
    runs a fast clean round. The deployment-wide usage_capable would wrongly arm
    stalled_no_progress on kimi; the per-agent axis must NOT."""
    _emit(
        tmp_path,
        events.ROUND_SUBSTRATE_BEFORE,
        _fake_clock,
        round_num=1,
        git_head="a",
        paths_hash=None,
    )
    _emit(
        tmp_path,
        events.AGENT_USAGE_RECORDED,
        _fake_clock,
        agent="pi",
        model="m",
        round_num=1,
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        cost_usd=None,
        duration_ms=10,
        success=True,
    )
    _emit(
        tmp_path,
        events.ROUND_SUBSTRATE_BEFORE,
        _fake_clock,
        round_num=2,
        git_head="b",
        paths_hash=None,
    )
    # kimi round 2: no usage event at all

    outcome = _round_outcome.round_outcome(tmp_path, ran_agent="kimi")
    no_prog = _round_outcome.round_had_no_progress(
        tmp_path,
        returncode=0,
        duration_s=2.0,
        threshold_s=30.0,
        throttle_active=False,
        outcome=outcome,
    )

    assert outcome.usage_capable is True  # deployment-wide: pi armed it
    assert outcome.usage_capable_by_agent.get("kimi", False) is False  # kimi never did
    assert no_prog is False  # per-agent: kimi is not usage-capable → not armed


def test_pi_round_should_be_no_progress_when_usageless(tmp_path, _fake_clock):
    """A usage-capable agent (pi) with NO usage this round IS no-progress."""
    _emit(
        tmp_path,
        events.ROUND_SUBSTRATE_BEFORE,
        _fake_clock,
        round_num=1,
        git_head="a",
        paths_hash=None,
    )
    _emit(
        tmp_path,
        events.AGENT_USAGE_RECORDED,
        _fake_clock,
        agent="pi",
        model="m",
        round_num=1,
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        cost_usd=None,
        duration_ms=10,
        success=True,
    )
    _emit(
        tmp_path,
        events.ROUND_SUBSTRATE_BEFORE,
        _fake_clock,
        round_num=2,
        git_head="b",
        paths_hash=None,
    )

    outcome = _round_outcome.round_outcome(tmp_path, ran_agent="pi")

    assert (
        _round_outcome.round_had_no_progress(
            tmp_path,
            returncode=0,
            duration_s=2.0,
            threshold_s=30.0,
            throttle_active=False,
            outcome=outcome,
        )
        is True
    )
