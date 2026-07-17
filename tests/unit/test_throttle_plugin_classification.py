"""A plugin-supplied transient-error classification must not crash the supervisor."""

from __future__ import annotations

import time
from pathlib import Path

from agent_runner._emit import emit_transient_error_detected
from agent_runner._throttle import _check_throttle_state, compute_adjusted_reset_at


def test_given_plugin_classification_when_computing_backoff_then_reset_honored_verbatim(
    tmp_log_dir: Path,
) -> None:
    """api_types.py types `classification` as str so plugins can supply their own.

    The plugin already supplied reset_at_epoch in its own event, so an unknown
    classification is honored verbatim — same contract as rate_limit_account.
    """
    reset_at = int(time.time()) + 60
    emit_transient_error_detected(
        tmp_log_dir,
        round_num=1,
        classification="aider_quota",
        reset_at_epoch=reset_at,
        agent="aider",
        raw="quota exceeded",
    )
    state = _check_throttle_state(tmp_log_dir)
    assert state is not None
    assert state.classification == "aider_quota"

    applied, count, capped = compute_adjusted_reset_at(
        classification=state.classification,
        original_reset_at_epoch=state.reset_at_epoch,
        agent="aider",
        log_dir=tmp_log_dir,
    )

    assert applied == reset_at  # honored verbatim, no invented back-off
    assert count == 0
    assert capped is False
