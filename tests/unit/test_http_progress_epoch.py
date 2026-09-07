from agent_runner import http_progress
from agent_runner.api_types import TransientErrorState


def test_rate_limit_state_survives_poisoned_epoch(tmp_path, monkeypatch):
    """A far-future reset_at_epoch must not OverflowError-crash the status page."""
    huge = TransientErrorState(
        reset_at_epoch=10**20,
        classification="rate_limit_account",
        agent="claude",
        since_round=1,
        phase="",
    )
    monkeypatch.setattr(
        http_progress, "effective_throttle_view", lambda _ld: (huge, {}), raising=False
    )
    # must return a dict (with a fallback iso), not raise OverflowError
    out = http_progress._rate_limit_state(tmp_path)
    assert out is not None and "throttled_until_iso" in out
    assert out["throttled_until_iso"] == "unknown"  # degraded display, not a crash
