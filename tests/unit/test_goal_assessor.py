"""assess_treadmill: the advisory-only treadmill core step. Fires an Advisory
IFF, across the last k=_TREADMILL_WINDOW_ROUNDS rounds, the tree kept moving
(CLI-agnostic activity signal) while every goal_check's satisfied/value stayed
put (no convergence) and no `goal_assessment` has already fired for this same
stuck signature (edge-trigger)."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runner.goal import _TREADMILL_WINDOW_ROUNDS, assess_treadmill


def _write(log_dir: Path, *evs: dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "events-2026-08.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for e in evs:
            f.write(json.dumps(e) + "\n")


def _round(
    n: int,
    *,
    git_head_before: str = "sha0",
    git_head_after: str = "sha0",
    dirty: bool = False,
    auto_committed: bool = False,
    check_satisfied: bool | None = False,
    check_value: float | None = 5.0,
    include_check: bool = True,
) -> list[dict]:
    evs: list[dict] = [
        {"event": "round_substrate_before", "round_num": n, "git_head": git_head_before},
        {"event": "round_start", "round_num": n},
        {"event": "agent_spawn", "round_num": n},
        {"event": "agent_exit", "round_num": n},
    ]
    if include_check:
        evs.append(
            {
                "event": "goal_check",
                "name": "lint",
                "satisfied": check_satisfied,
                "value": check_value,
                "timed_out": False,
                "skipped": False,
            }
        )
    if dirty:
        evs.append({"event": "dirty_detected", "round_num": n, "files": ["a.py"]})
    if auto_committed:
        evs.append({"event": "dirty_auto_committed", "round_num": n, "files": ["a.py"], "ref": "x"})
    evs.append({"event": "round_substrate_after", "round_num": n, "git_head": git_head_after})
    evs.append({"event": "round_end", "round_num": n})
    return evs


def test_treadmill_window_rounds_should_be_three() -> None:
    assert _TREADMILL_WINDOW_ROUNDS == 3


def test_assess_treadmill_should_return_advisory_when_dirty_and_check_unchanged(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True))

    advisory = assess_treadmill(log_dir)

    assert advisory is not None
    assert advisory.confidence in ("low", "medium", "high")
    assert advisory.observation
    assert advisory.question
    # Type-structural firewall: no kill/severity/action/terminate field exists
    # on the dataclass at all -- not merely unset.
    field_names = {f.name for f in __import__("dataclasses").fields(advisory)}
    assert field_names == {"observation", "question", "confidence"}


def test_assess_treadmill_should_return_advisory_on_auto_committed_activity_too(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    for n in (1, 2, 3):
        _write(log_dir, *_round(n, auto_committed=True))

    assert assess_treadmill(log_dir) is not None


def test_assess_treadmill_should_return_advisory_on_git_head_movement_with_no_dirty_event(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    for n in (1, 2, 3):
        _write(log_dir, *_round(n, git_head_before=f"sha{n}", git_head_after=f"sha{n}moved"))

    assert assess_treadmill(log_dir) is not None


def test_assess_treadmill_should_return_none_when_no_activity(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=False))  # git_head before==after too

    assert assess_treadmill(log_dir) is None


def test_assess_treadmill_should_return_none_when_check_value_converges(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    for n, value in zip((1, 2, 3), (5.0, 4.0, 3.0), strict=True):
        _write(log_dir, *_round(n, dirty=True, check_value=value))

    assert assess_treadmill(log_dir) is None


def test_assess_treadmill_should_return_none_when_check_satisfied_flips(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    for n, satisfied in zip((1, 2, 3), (False, False, True), strict=True):
        _write(log_dir, *_round(n, dirty=True, check_satisfied=satisfied))

    assert assess_treadmill(log_dir) is None


def test_assess_treadmill_should_return_none_when_already_fired_for_this_signature(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True))
    _write(
        log_dir,
        {
            "event": "goal_assessment",
            "observation": "already told you",
            "question": "still stuck?",
            "confidence": "medium",
        },
    )

    assert assess_treadmill(log_dir) is None


def test_assess_treadmill_should_return_none_when_fewer_than_k_rounds_exist(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    for n in (1, 2):
        _write(log_dir, *_round(n, dirty=True))

    assert assess_treadmill(log_dir) is None


def test_assess_treadmill_should_fire_with_no_agent_usage_recorded_event_anywhere(
    tmp_path: Path,
) -> None:
    """CLI-agnostic: a custom [agent] command whose plugin stack never emits
    agent_usage_recorded must still trip the treadmill assessor -- it reads
    only dirty/git-head/goal_check, never agent_usage_recorded."""
    log_dir = tmp_path / "logs"
    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True))

    all_lines = (log_dir / "events-2026-08.jsonl").read_text(encoding="utf-8")
    assert "agent_usage_recorded" not in all_lines  # vacuity-guard on the test's own setup

    assert assess_treadmill(log_dir) is not None
