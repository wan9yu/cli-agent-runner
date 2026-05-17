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
                "event": "rate_limit_rejected",
                "ts": "2026-05-16T00:00:00Z",
                "agent": "claude",
                "reset_at_epoch": future,
                "limit_type": "five_hour",
                "round_num": 42,
            }
        ],
    )
    state = _check_throttle_state(tmp_path)
    assert state is not None
    assert state.reset_at_epoch == future
    # Old rate_limit_rejected events imply rate_limit_account classification
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
                "event": "rate_limit_rejected",
                "ts": "2026-05-16T00:00:00Z",
                "agent": "claude",
                "reset_at_epoch": future,
                "limit_type": "five_hour",
                "round_num": 42,
            },
            {
                "event": "rate_limit_recovered",
                "ts": "2026-05-16T00:01:00Z",
                "agent": "claude",
                "throttled_for_s": 60,
                "limit_type": "five_hour",
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
                "event": "rate_limit_rejected",
                "ts": "2026-05-16T00:00:00Z",
                "agent": "claude",
                "reset_at_epoch": past,
                "limit_type": "five_hour",
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
