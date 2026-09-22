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


def test_round_result_should_have_all_required_fields_when_inspected() -> None:
    actual = {f.name for f in fields(RoundResult)}

    missing = REQUIRED_FIELDS - actual

    assert not missing, f"RoundResult missing fields: {missing}"


def test_round_result_should_have_matching_simple_types_when_inspected() -> None:
    hints = get_type_hints(RoundResult)

    simple = {
        "round_num": int,
        "started_at": str,
        "ended_at": str,
        "exit_code": int,
        "duration_s": float,
        "timed_out": bool,
        "log_path": Path,
        "stashed": bool,
    }

    assert all(hints[name] is typ for name, typ in simple.items())
    assert typing.get_origin(hints["dirty_files"]) is list


def test_round_result_phase_should_be_optional_str_when_inspected() -> None:
    hints = get_type_hints(RoundResult)

    args = typing.get_args(hints["phase"])

    assert str in args and type(None) in args, f"phase should be str | None, got {hints['phase']}"


def test_round_result_should_be_frozen_when_constructed() -> None:
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

    assert r.round_num == 1
