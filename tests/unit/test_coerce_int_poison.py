from __future__ import annotations

import json
import warnings
from pathlib import Path

from agent_runner._throttle import _check_throttle_state, _coerce_int
from tests._clock import FakeClock


def test_coerce_int_variants() -> None:
    assert _coerce_int(5, 0) == 5
    assert _coerce_int(5.9, 0) == 5
    assert _coerce_int("7", 0) == 7
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert _coerce_int(None, 0) == 0
        assert _coerce_int("abc", 3) == 3
    assert len(w) == 2  # None and non-numeric both warn


def test_null_reset_at_epoch_leaves_serve_unthrottled_and_warns(tmp_path: Path) -> None:
    ev = {
        "event": "transient_error_detected",
        "ts": "2026-08-30T00:00:00Z",
        "agent": "claude",
        "reset_at_epoch": None,
        "classification": "rate_limit_account",
        "round_num": 4,
    }
    (tmp_path / "events-2026-08.jsonl").write_text(json.dumps(ev) + "\n")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        state = _check_throttle_state(tmp_path, clock=FakeClock())
    assert state is None  # unthrottled, no TypeError out of the serve loop
    assert any("reset_at_epoch" in str(x.message) or "coerce" in str(x.message).lower() for x in w)
