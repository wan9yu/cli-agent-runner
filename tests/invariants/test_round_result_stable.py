"""Invariant: RoundResult fields and types must not regress.

Plugin authors writing PostRoundHook depend on this shape being stable
across 0.1.x minor releases. Adding fields is fine; removing or retyping
is a breaking change that requires a major bump.
"""

from __future__ import annotations

import typing
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import get_type_hints

import pytest

from agent_runner.api_types import RoundResult

REQUIRED_FIELDS: set[str] = {
    "round_num",
    "phase",
    "started_at",
    "ended_at",
    "exit_code",
    "duration_s",
    "timed_out",
    "log_path",
    "dirty_files",
    "stashed",
    "dirty_outcome",
}


def test_given_round_result_when_inspected_then_all_required_fields_present() -> None:
    actual = {f.name for f in fields(RoundResult)}
    missing = REQUIRED_FIELDS - actual
    assert not missing, f"RoundResult missing fields: {missing}"


def test_given_round_result_when_inspected_then_simple_types_match() -> None:
    hints = get_type_hints(RoundResult)
    assert hints["round_num"] is int
    assert hints["started_at"] is str
    assert hints["ended_at"] is str
    assert hints["exit_code"] is int
    assert hints["duration_s"] is float
    assert hints["timed_out"] is bool
    assert hints["log_path"] is Path
    assert hints["stashed"] is bool
    # dirty_files is list[str] — check origin
    assert typing.get_origin(hints["dirty_files"]) is list


def test_given_round_result_when_inspected_then_phase_is_optional_str() -> None:
    hints = get_type_hints(RoundResult)
    # `str | None` is a Union of (str, NoneType)
    args = typing.get_args(hints["phase"])
    assert str in args, f"phase should accept str, got {hints['phase']}"
    assert type(None) in args, f"phase should accept None, got {hints['phase']}"


def test_given_round_result_when_constructed_then_frozen() -> None:
    r = RoundResult(
        round_num=1,
        phase=None,
        started_at="2026-01-01T00:00:00.000Z",
        ended_at="2026-01-01T00:01:00.000Z",
        exit_code=0,
        duration_s=60.0,
        timed_out=False,
        log_path=Path("/tmp/round.log"),
        dirty_files=[],
        stashed=False,
    )
    with pytest.raises(FrozenInstanceError):
        r.round_num = 99  # type: ignore[misc]
