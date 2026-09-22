"""assess_treadmill: the advisory-only treadmill core step. Fires an Advisory
IFF, across the last k=_TREADMILL_WINDOW_ROUNDS COMPLETED rounds strictly
before `current_round`, the tree kept moving (CLI-agnostic activity signal)
while every goal_check's satisfied/value stayed put (no convergence) and no
`goal_assessment` has already fired for this same stuck EPISODE (edge-trigger,
keyed off where the episode actually started, not merely the sliding window).

Every fixture below ends with a lone trailing `round_substrate_before` for
`current_round` and no further events -- that's the REAL on-disk shape at the
call site: serve emits `round_substrate_before(round_num=current_round)`
before spawning the round child that runs this assessor (see
`cli/serve_cmd.py`'s `_capture_substrate` + `_spawn_round`), so the
in-progress round's own substrate-before is always already in the tail."""

from __future__ import annotations

import dataclasses
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


def _pending_substrate_before(n: int, git_head: str = "sha0") -> dict:
    """The in-progress round's own pre-spawn marker -- the real on-disk tail
    shape `assess_treadmill` sees when `serve` calls it for round `n`."""
    return {"event": "round_substrate_before", "round_num": n, "git_head": git_head}


_GOAL_ASSESSMENT_MARKER = {
    "event": "goal_assessment",
    "observation": "already told you",
    "question": "still stuck?",
    "confidence": "medium",
}


def test_treadmill_window_rounds_should_be_three_when_invoked() -> None:

    actual = _TREADMILL_WINDOW_ROUNDS

    expected = 3

    assert actual == expected


def test_assess_treadmill_should_return_advisory_when_dirty_and_check_unchanged(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True))
    _write(log_dir, _pending_substrate_before(4))

    advisory = assess_treadmill(log_dir, current_round=4)

    assert advisory is not None
    assert advisory.confidence == "medium"
    assert "never changed" in advisory.observation
    assert "blocking progress" in advisory.question
    # Type-structural firewall: no kill/severity/action/terminate field exists
    # on the dataclass at all -- not merely unset.
    field_names = {f.name for f in dataclasses.fields(advisory)}
    assert field_names == {"observation", "question", "confidence"}


def test_assess_treadmill_should_ignore_in_progress_round_substrate_before_landed_when_invoked(
    tmp_path: Path,
) -> None:
    """CRITICAL regression pin: under `serve`, round_substrate_before(N) for
    the round CURRENTLY being assessed is already in the tail (emitted
    pre-spawn) by the time the round child calls this. Without `current_round`
    excluding it, round N would be treated as the newest (and only) round in
    the window with zero activity recorded yet, and gate 1 would fail on
    every single call -- the assessor would be dark on the real `serve` path
    even though every unit test using a round-N-omitted fixture kept passing."""
    log_dir = tmp_path / "logs"

    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True))
    _write(log_dir, _pending_substrate_before(4))

    actual = assess_treadmill(log_dir, current_round=4)

    assert actual is not None
    assert actual.confidence == "medium"
    assert "never changed" in actual.observation


def test_assess_treadmill_should_return_advisory_on_auto_committed_activity_too_when_invoked(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"

    for n in (1, 2, 3):
        _write(log_dir, *_round(n, auto_committed=True))
    _write(log_dir, _pending_substrate_before(4))

    actual = assess_treadmill(log_dir, current_round=4)

    assert actual is not None
    assert actual.confidence == "medium"
    assert "never changed" in actual.observation


def test_assess_treadmill_should_return_advisory_on_git_head_movement_with_no_dirty_event_when_run(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"

    for n in (1, 2, 3):
        _write(log_dir, *_round(n, git_head_before=f"sha{n}", git_head_after=f"sha{n}moved"))
    _write(log_dir, _pending_substrate_before(4))

    actual = assess_treadmill(log_dir, current_round=4)

    assert actual is not None
    assert actual.confidence == "medium"
    assert "never changed" in actual.observation


def test_assess_treadmill_should_return_none_when_no_activity(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"

    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=False))  # git_head before==after too
    _write(log_dir, _pending_substrate_before(4))

    assert assess_treadmill(log_dir, current_round=4) is None


def test_assess_treadmill_should_return_none_when_check_value_converges(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"

    for n, value in zip((1, 2, 3), (5.0, 4.0, 3.0), strict=True):
        _write(log_dir, *_round(n, dirty=True, check_value=value))
    _write(log_dir, _pending_substrate_before(4))

    assert assess_treadmill(log_dir, current_round=4) is None


def test_assess_treadmill_should_return_none_when_check_satisfied_flips(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"

    for n, satisfied in zip((1, 2, 3), (False, False, True), strict=True):
        _write(log_dir, *_round(n, dirty=True, check_satisfied=satisfied))
    _write(log_dir, _pending_substrate_before(4))

    assert assess_treadmill(log_dir, current_round=4) is None


def test_assess_treadmill_should_return_none_when_every_check_is_satisfied_and_stable(
    tmp_path: Path,
) -> None:
    """A goal that's MET and stable (every check satisfied, unchanged across
    the window) must never be misread as stuck -- a constant signature alone
    doesn't mean treadmilling, it can just as well mean the goal was met and
    the agent kept working past it. Regression pin for the false-steer where
    gate 2 only checked for a CHANGED signature, never for an UNSATISFIED
    one."""
    log_dir = tmp_path / "logs"

    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True, check_satisfied=True))
    _write(log_dir, _pending_substrate_before(4))

    assert assess_treadmill(log_dir, current_round=4) is None


def test_assess_treadmill_should_return_none_when_already_fired_for_this_signature(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"

    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True))
    _write(log_dir, _GOAL_ASSESSMENT_MARKER)
    _write(log_dir, _pending_substrate_before(4))

    assert assess_treadmill(log_dir, current_round=4) is None


def test_assess_treadmill_should_return_none_when_fewer_than_k_rounds_exist(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"

    for n in (1, 2):
        _write(log_dir, *_round(n, dirty=True))
    _write(log_dir, _pending_substrate_before(3))

    assert assess_treadmill(log_dir, current_round=3) is None


def test_assess_treadmill_should_fire_with_no_agent_usage_recorded_event_anywhere_when_invoked(
    tmp_path: Path,
) -> None:
    """CLI-agnostic: a custom [agent] command whose plugin stack never emits
    agent_usage_recorded must still trip the treadmill assessor -- it reads
    only dirty/git-head/goal_check, never agent_usage_recorded."""
    log_dir = tmp_path / "logs"
    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True))
    _write(log_dir, _pending_substrate_before(4))

    all_lines = (log_dir / "events-2026-08.jsonl").read_text(encoding="utf-8")
    assert "agent_usage_recorded" not in all_lines  # vacuity-guard on the test's own setup

    actual = assess_treadmill(log_dir, current_round=4)

    assert actual is not None
    assert "never changed" in actual.observation


def test_assess_treadmill_should_not_refire_when_the_episode_outlasts_the_window(
    tmp_path: Path,
) -> None:
    """IMPORTANT regression pin: the edge-trigger is keyed off the EPISODE
    (where the stuck signature actually began), not the sliding k-round
    window. Six rounds (k+3, with the default k=3) of an unbroken treadmill,
    with the advisory already fired once right after round 3 -- by round 7
    the window [4,5,6] no longer contains round 1..3, but the whole 1..6
    stretch is still ONE unbroken episode, so this must NOT refire."""
    log_dir = tmp_path / "logs"

    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True))
    _write(log_dir, _GOAL_ASSESSMENT_MARKER)
    for n in (4, 5, 6):
        _write(log_dir, *_round(n, dirty=True))
    _write(log_dir, _pending_substrate_before(7))

    assert assess_treadmill(log_dir, current_round=7) is None


def test_assess_treadmill_should_rearm_after_a_check_value_moves_when_invoked(
    tmp_path: Path,
) -> None:
    """A check's value moving breaks the episode -- the OLD fired-marker no
    longer covers the NEW stuck signature that follows it, so this must
    re-fire once the new signature has held for a full window."""
    log_dir = tmp_path / "logs"

    for n in (1, 2, 3):
        _write(log_dir, *_round(n, dirty=True, check_value=5.0))
    _write(log_dir, _GOAL_ASSESSMENT_MARKER)
    _write(log_dir, *_round(4, dirty=True, check_value=4.0))  # the move -- breaks the episode
    for n in (5, 6, 7):
        _write(log_dir, *_round(n, dirty=True, check_value=3.0))  # new episode, new value
    _write(log_dir, _pending_substrate_before(8))

    actual = assess_treadmill(log_dir, current_round=8)

    assert actual is not None
    assert "never changed" in actual.observation
