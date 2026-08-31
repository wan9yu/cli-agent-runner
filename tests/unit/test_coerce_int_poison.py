from __future__ import annotations

import json
import math
import warnings
from pathlib import Path

from agent_runner._throttle import _check_throttle_state, _coerce_float, _coerce_int
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


def test_coerce_int_and_float_reject_nan_and_infinity() -> None:
    """json.loads accepts bare NaN/Infinity/-Infinity tokens by default; both
    coercers must treat them as non-numeric (warn + default), never let
    int()/float() raise (ValueError on NaN, OverflowError on +-Infinity for int())."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert _coerce_int(math.nan, 0) == 0
        assert _coerce_int(math.inf, 0) == 0
        assert _coerce_int(-math.inf, 0) == 0
        assert _coerce_float(math.nan, 0.0) == 0.0
        assert _coerce_float(math.inf, 0.0) == 0.0
        assert _coerce_float("Infinity", 0.0) == 0.0  # string-parsed path too
    assert len(w) == 6


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


def test_nan_reset_at_epoch_leaves_serve_unthrottled_and_warns(tmp_path: Path) -> None:
    """A plugin-written reset_at_epoch: NaN must not raise ValueError out of
    _check_throttle_state (int(float('nan')) raises without the isfinite guard)."""
    ev = {
        "event": "transient_error_detected",
        "ts": "2026-08-30T00:00:00Z",
        "agent": "claude",
        "reset_at_epoch": float("nan"),
        "classification": "rate_limit_account",
        "round_num": 4,
    }
    text = json.dumps(ev) + "\n"
    assert "NaN" in text  # confirm the literal token, not a stringified "nan"
    (tmp_path / "events-2026-08.jsonl").write_text(text)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        state = _check_throttle_state(tmp_path, clock=FakeClock())
    assert state is None  # unthrottled, no ValueError out of the serve loop
    assert any("coerce" in str(x.message).lower() for x in w)


def test_infinity_reset_at_epoch_leaves_serve_unthrottled_and_warns(tmp_path: Path) -> None:
    """A plugin-written reset_at_epoch: Infinity must not raise OverflowError out of
    _check_throttle_state (int(float('inf')) raises without the isfinite guard)."""
    ev = {
        "event": "transient_error_detected",
        "ts": "2026-08-30T00:00:00Z",
        "agent": "claude",
        "reset_at_epoch": float("inf"),
        "classification": "rate_limit_account",
        "round_num": 4,
    }
    text = json.dumps(ev) + "\n"
    assert "Infinity" in text  # confirm the literal token, not a stringified "inf"
    (tmp_path / "events-2026-08.jsonl").write_text(text)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        state = _check_throttle_state(tmp_path, clock=FakeClock())
    assert state is None  # unthrottled, no OverflowError out of the serve loop
    assert any("coerce" in str(x.message).lower() for x in w)
