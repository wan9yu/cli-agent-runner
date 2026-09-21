"""run_goal_checks runs each [[goal.checks]] entry via run_bounded and emits
one goal_check event per check -- reap-safe, timeout-bounded, and a dry_run
that never spawns a subprocess."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_runner import events
from agent_runner import goal as goal_module
from agent_runner.config import GoalConfig, _GoalCheckConfig
from agent_runner.goal import run_goal_checks


def _events(log_dir: Path) -> list[dict]:
    files = list(log_dir.glob("events-*.jsonl"))
    assert len(files) == 1
    return list(events.iter_event_dicts(files[0]))


def _check(
    name: str, cmd: list[str], *, timeout_s: int = 10, cwd: str | None = None
) -> _GoalCheckConfig:
    return _GoalCheckConfig(name=name, cmd=cmd, cwd=cwd, timeout_s=timeout_s)


def test_run_goal_checks_should_emit_satisfied_true_when_check_exits_zero(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    goal = GoalConfig(checks=(_check("lint", ["sh", "-c", "exit 0"]),), ledger="ledger.md")

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)

    [ev] = _events(tmp_log_dir)
    assert ev["event"] == "goal_check"
    assert ev["name"] == "lint"
    assert ev["satisfied"] is True
    assert ev["timed_out"] is False
    assert ev["skipped"] is False


def test_run_goal_checks_should_emit_satisfied_false_and_parsed_value_when_check_exits_nonzero(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    goal = GoalConfig(
        checks=(_check("coverage", ["sh", "-c", "echo 3; exit 1"]),), ledger="ledger.md"
    )

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)

    [ev] = _events(tmp_log_dir)
    assert ev["satisfied"] is False
    assert ev["value"] == 3.0
    assert ev["timed_out"] is False


def test_run_goal_checks_should_emit_timed_out_when_check_exceeds_its_timeout(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    goal = GoalConfig(checks=(_check("slow", ["sleep", "30"], timeout_s=1),), ledger="ledger.md")

    started = time.monotonic()
    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)
    elapsed = time.monotonic() - started

    assert elapsed < 2.5, (
        f"took {elapsed:.1f}s -- expected a prompt SIGTERM death, not the full grace"
    )
    [ev] = _events(tmp_log_dir)
    assert ev["timed_out"] is True
    assert ev["satisfied"] is False


def test_run_goal_checks_should_emit_skipped_and_never_spawn_when_dry_run(
    tmp_log_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = MagicMock(side_effect=AssertionError("run_bounded must not be called in dry_run"))
    monkeypatch.setattr(goal_module, "run_bounded", spawn)
    goal = GoalConfig(checks=(_check("lint", ["sh", "-c", "exit 0"]),), ledger="ledger.md")

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=True)

    spawn.assert_not_called()
    [ev] = _events(tmp_log_dir)
    assert ev["name"] == "lint"
    assert ev["skipped"] is True
    assert "satisfied" not in ev
    assert "value" not in ev
    assert "timed_out" not in ev


def test_run_goal_checks_should_emit_one_event_per_check_when_multiple_checks_configured(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    goal = GoalConfig(
        checks=(
            _check("a", ["sh", "-c", "exit 0"]),
            _check("b", ["sh", "-c", "exit 1"]),
        ),
        ledger="ledger.md",
    )

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)

    evs = _events(tmp_log_dir)
    assert [e["name"] for e in evs] == ["a", "b"]
    assert [e["satisfied"] for e in evs] == [True, False]


def test_run_goal_checks_should_emit_unsatisfied_when_command_missing(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    """A misconfigured check (typo'd binary, missing from PATH) must not
    crash the round child -- run_bounded's own subprocess.Popen raises OSError
    before its timeout machinery ever engages; the check just reads as
    unsatisfied, advisory-only."""
    goal = GoalConfig(
        checks=(_check("missing", ["definitely-not-a-binary-xyz"]),), ledger="ledger.md"
    )

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)

    [ev] = _events(tmp_log_dir)
    assert ev["event"] == "goal_check"
    assert ev["name"] == "missing"
    assert ev["satisfied"] is False
    assert ev["value"] is None
    assert ev["timed_out"] is False
    assert ev["skipped"] is False
    assert "error" in ev


def test_run_goal_checks_should_resolve_relative_cwd_against_work_dir_when_invoked(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "marker.txt").write_text("x")
    goal = GoalConfig(
        checks=(_check("in-sub", ["sh", "-c", "test -f marker.txt"], cwd="sub"),),
        ledger="ledger.md",
    )

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)

    [ev] = _events(tmp_log_dir)
    assert ev["satisfied"] is True


def test_resolve_check_cwd_should_keep_absolute_cwd_as_is_when_invoked(tmp_path: Path) -> None:
    from agent_runner.goal import _resolve_check_cwd

    absolute = tmp_path / "elsewhere"

    resolved = _resolve_check_cwd(str(absolute), tmp_path / "work")

    assert resolved == absolute


def test_run_goal_checks_should_treat_non_finite_value_as_unparsed_when_invoked(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    """nan isn't even equal to itself -- the treadmill assessor's "did the
    value change" comparison must never see one."""
    goal = GoalConfig(
        checks=(_check("nan-check", ["sh", "-c", "echo nan; exit 1"]),), ledger="ledger.md"
    )

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)

    [ev] = _events(tmp_log_dir)
    assert ev["value"] is None


def test_run_goal_checks_should_not_crash_when_check_emits_non_utf8_output(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    """Fail-open on non-UTF-8 output: a check emitting a non-UTF-8 byte must not
    raise a UnicodeDecodeError (a non-OSError) past run_goal_checks' guard and
    read as a round crash. INDEPENDENT lock on errors="replace" -- not merely on
    run_goal_checks' own guard: UnicodeDecodeError is a ValueError subclass, so
    the executor's (OSError, ValueError) catch would ALSO swallow it cleanly
    (satisfied=False, timed_out=False, error=<str>) if errors="replace" were
    dropped from _bounded's Popen -- a bare satisfied/timed_out assertion
    would pass under that mutation too. The trailing parseable token proves
    the check's stdout was actually READ (degraded, not lost): asserting the
    parsed value AND the absence of an `error` field fails under the mutation,
    since the guard's own emit sets value=None and a non-empty `error`."""
    goal = GoalConfig(
        checks=(_check("latin1", ["sh", "-c", "printf '\\377 7'; exit 1"]),), ledger="ledger.md"
    )

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)  # must not raise

    [ev] = _events(tmp_log_dir)
    assert ev["event"] == "goal_check"
    assert ev["satisfied"] is False
    assert ev["timed_out"] is False
    assert "error" not in ev
    assert ev["value"] == 7.0


def test_run_goal_checks_should_report_unsatisfied_when_cmd_contains_a_nul_byte(
    tmp_log_dir: Path, tmp_path: Path
) -> None:
    """A NUL byte in a check's argv makes subprocess.Popen raise
    ``ValueError: embedded null byte`` -- a non-OSError -- BEFORE any timeout
    machinery. The executor guard catches it and reports the check unsatisfied
    rather than crashing the round. Mutation check: narrowing the guard back to
    ``except OSError`` lets the ValueError escape and this test errors out."""
    goal = GoalConfig(checks=(_check("nul", ["echo", "a\x00b"]),), ledger="ledger.md")

    run_goal_checks(goal, work_dir=tmp_path, log_dir=tmp_log_dir, dry_run=False)  # must not raise

    [ev] = _events(tmp_log_dir)
    assert ev["event"] == "goal_check"
    assert ev["satisfied"] is False
    assert "error" in ev
