"""Tests for agent_runner.round_log helper functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.round_log import (
    ROUND_CURRENT_LINK,
    atomic_relink,
    next_round_num,
    prune_old_round_logs,
    prune_rounds_dir,
)


def test_given_no_log_files_when_next_round_num_then_returns_one(tmp_path: Path) -> None:
    """Empty log_dir → next round num is 1."""
    assert next_round_num(tmp_path) == 1


def test_given_existing_round_files_when_next_round_num_then_skips_past_max(
    tmp_path: Path,
) -> None:
    """next_round_num returns max(status, file_max) + 1 — file fallback wins when status absent."""
    (tmp_path / "round-5.log").write_text("x")
    (tmp_path / "round-7.log").write_text("x")
    assert next_round_num(tmp_path) == 8


def test_given_target_when_atomic_relink_then_symlink_replaced(tmp_path: Path) -> None:
    """atomic_relink replaces an existing symlink atomically."""
    target1 = tmp_path / "a.log"
    target1.write_text("a")
    target2 = tmp_path / "b.log"
    target2.write_text("b")
    link = tmp_path / "current.log"
    # Create initial symlink
    atomic_relink(link, target1)
    assert link.is_symlink()
    assert link.resolve() == target1.resolve()
    # Replace
    atomic_relink(link, target2)
    assert link.resolve() == target2.resolve()


def _write_round_logs(dir_path: Path, count: int, *, first: int = 1) -> None:
    """Create ``count`` serve-level round-<N>.log files with ascending mtimes."""
    import os

    for i in range(first, first + count):
        path = dir_path / f"round-{i}.log"
        path.write_text(f"r{i}")
        os.utime(path, (1000000.0 + i, 1000000.0 + i))


def _write_agent_round_logs(dir_path: Path, count: int, *, first: int = 1) -> None:
    """Create ``count`` agent transcripts named R<N>-<timestamp>.log."""
    for i in range(first, first + count):
        (dir_path / f"R{i}-20260101T000000.log").write_text(f"r{i}")


def test_given_many_round_files_when_prune_then_only_recent_kept(tmp_path: Path) -> None:
    """prune_old_round_logs keeps most-recent N by mtime."""
    _write_round_logs(tmp_path, 6)

    outcome = prune_old_round_logs(tmp_path, retention=3)

    # rounds 4, 5, 6 (most recent by mtime) survive; 1, 2, 3 pruned
    assert outcome.deleted == 3
    assert outcome.deferred == 0
    assert not (tmp_path / "round-1.log").exists()
    assert not (tmp_path / "round-2.log").exists()
    assert not (tmp_path / "round-3.log").exists()
    assert (tmp_path / "round-4.log").exists()
    assert (tmp_path / "round-5.log").exists()
    assert (tmp_path / "round-6.log").exists()


def test_given_symlink_when_prune_then_symlink_excluded(tmp_path: Path) -> None:
    """The round-current.log symlink is not counted toward retention nor pruned."""
    _write_round_logs(tmp_path, 3)
    # Create symlink to round-3 (newest)
    atomic_relink(tmp_path / ROUND_CURRENT_LINK, tmp_path / "round-3.log")

    outcome = prune_old_round_logs(tmp_path, retention=2)

    # Only round-1 goes: the symlink is excluded before the retention slice, so
    # round-2 is inside the kept window rather than displaced by it.
    assert outcome.existing == 3
    assert outcome.deleted == 1
    assert (tmp_path / "round-3.log").exists()
    assert (tmp_path / "round-2.log").exists()
    # symlink intact
    assert (tmp_path / ROUND_CURRENT_LINK).is_symlink()
    assert not (tmp_path / "round-1.log").exists()


def test_given_bulk_backlog_when_prune_old_round_logs_then_nothing_deleted(
    tmp_path: Path,
) -> None:
    """The bulk guard covers the serve-level family too: one knob, one contract.

    An operator who drastically lowers round_log_retention has the same
    exposure here as in rounds/ — the deletion is deferred, not performed.
    """
    _write_round_logs(tmp_path, 60)

    outcome = prune_old_round_logs(tmp_path, retention=10)

    assert outcome.deleted == 0
    assert outcome.deferred == 50
    assert outcome.existing == 60
    assert len(list(tmp_path.glob("round-*.log"))) == 60


def test_given_file_vanishes_between_glob_and_stat_when_prune_then_skips_not_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A round-*.log deleted by a concurrent cleanup/logrotate between glob()
    enumeration and the stat() sort key is a TOCTOU race, not a permanent
    failure. It must be skipped, not raise FileNotFoundError — that OSError
    would otherwise reach serve_cmd._prepare_loop's deterministic-startup
    guard and misclassify a transient race as a permanent config failure
    (PERMANENT_CONFIG_EXIT / no systemd restart)."""
    _write_round_logs(tmp_path, 3)
    vanished = tmp_path / "round-2.log"
    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == vanished:
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    outcome = prune_old_round_logs(tmp_path, retention=1)

    # round-2 (the "vanished" one) is skipped entirely — not counted, not
    # pruned. Of the remaining two, round-3 (newest) survives the retention=1
    # keep and round-1 (oldest) is pruned as stale.
    assert outcome.existing == 2
    assert outcome.deleted == 1
    assert outcome.deferred == 0
    assert (tmp_path / "round-3.log").exists()
    assert not (tmp_path / "round-1.log").exists()


def test_given_dangling_symlink_when_prune_then_no_crash_and_skipped(tmp_path: Path) -> None:
    """A round-*.log symlink whose target is gone must not raise during the
    mtime sort (p.stat() follows the link → FileNotFoundError) and must never
    be a deletion candidate."""
    _write_round_logs(tmp_path, 3)  # round-1..3
    dangling = tmp_path / "round-9.log"
    dangling.symlink_to(tmp_path / "gone-target.log")  # target does not exist

    outcome = prune_old_round_logs(tmp_path, retention=2)

    assert dangling.is_symlink()  # untouched
    assert not (tmp_path / "round-1.log").exists()  # oldest regular file pruned
    assert outcome.deleted == 1


def test_given_dangling_round_current_link_when_prune_then_no_crash_and_left_alone(
    tmp_path: Path,
) -> None:
    """Finding #13, literally: round-current.log itself (not a stand-in name)
    is a symlink whose target has been removed. Must not crash serve startup,
    must never be counted or pruned, and the real round logs still prune
    correctly per retention."""
    _write_round_logs(tmp_path, 3)  # round-1..3
    dangling_current = tmp_path / ROUND_CURRENT_LINK
    dangling_current.symlink_to(tmp_path / "round-removed.log")  # target does not exist

    outcome = prune_old_round_logs(tmp_path, retention=2)  # must not raise

    assert dangling_current.is_symlink()  # untouched, not resolved/crashed on
    assert outcome.existing == 3  # dangling round-current.log never counted
    assert outcome.deleted == 1
    assert not (tmp_path / "round-1.log").exists()  # oldest regular file pruned
    assert (tmp_path / "round-2.log").exists()
    assert (tmp_path / "round-3.log").exists()


def test_given_file_deleted_during_sort_when_prune_then_skips_entry(
    tmp_path: Path, monkeypatch
) -> None:
    """TOCTOU: a regular round-*.log vanishing between glob and lstat is skipped,
    not raised."""
    _write_round_logs(tmp_path, 3)
    real_lstat = Path.lstat

    def flaky_lstat(self):
        if self.name == "round-2.log":
            raise FileNotFoundError(self)
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", flaky_lstat)
    outcome = prune_old_round_logs(tmp_path, retention=1)  # must not raise
    assert outcome.deleted >= 1


def test_given_retention_zero_when_prune_old_round_logs_then_never_prunes(
    tmp_path: Path,
) -> None:
    """0 = never prune, the default. Not a deferral: no backlog to report."""
    _write_round_logs(tmp_path, 50)

    outcome = prune_old_round_logs(tmp_path, retention=0)

    assert outcome.deleted == 0
    assert outcome.deferred == 0
    assert len(list(tmp_path.glob("round-*.log"))) == 50


def test_given_retention_zero_when_prune_rounds_dir_then_never_prunes(tmp_path: Path) -> None:
    """0 = never prune. ``deferred`` stays 0 so no event is emitted: 0 is the
    operator's expressed intent, not a backlog awaiting a decision."""
    _write_agent_round_logs(tmp_path, 500)

    outcome = prune_rounds_dir(tmp_path, keep=0)

    assert outcome.deleted == 0
    assert outcome.deferred == 0
    assert len(list(tmp_path.glob("R*.log"))) == 500


def test_given_bulk_backlog_when_prune_rounds_dir_then_nothing_deleted(tmp_path: Path) -> None:
    """A prune that would remove more than it keeps deletes NOTHING and reports.

    The 0.2.4 regression: the first post-upgrade round on a deployment with a
    12k-file backlog silently deleted the entire history.
    """
    _write_agent_round_logs(tmp_path, 300)

    outcome = prune_rounds_dir(tmp_path, keep=100)

    assert outcome.deleted == 0
    assert outcome.deferred == 200
    assert outcome.existing == 300
    assert len(list(tmp_path.glob("R*.log"))) == 300


def test_given_steady_state_backlog_when_prune_rounds_dir_then_one_deleted(
    tmp_path: Path,
) -> None:
    """Normal operation is untouched: one round over retention prunes one file."""
    _write_agent_round_logs(tmp_path, 101)

    outcome = prune_rounds_dir(tmp_path, keep=100)

    assert outcome.deleted == 1
    assert outcome.deferred == 0
    assert outcome.existing == 101
    assert not (tmp_path / "R1-20260101T000000.log").exists()


def test_given_stale_equal_to_keep_when_prune_rounds_dir_then_deletes(tmp_path: Path) -> None:
    """Boundary: stale == keep is not bulk — the prune runs."""
    _write_agent_round_logs(tmp_path, 10)

    outcome = prune_rounds_dir(tmp_path, keep=5)

    assert outcome.deleted == 5
    assert outcome.deferred == 0
    assert len(list(tmp_path.glob("R*.log"))) == 5


def test_given_stale_one_over_keep_when_prune_rounds_dir_then_defers(tmp_path: Path) -> None:
    """Boundary: stale == keep + 1 is bulk — nothing is deleted."""
    _write_agent_round_logs(tmp_path, 11)

    outcome = prune_rounds_dir(tmp_path, keep=5)

    assert outcome.deleted == 0
    assert outcome.deferred == 6
    assert len(list(tmp_path.glob("R*.log"))) == 11


def test_given_retention_raised_above_backlog_when_prune_rounds_dir_then_no_deferral(
    tmp_path: Path,
) -> None:
    """The operator's escape hatch: raise retention past the backlog and the
    guard stops tripping without any file being deleted."""
    _write_agent_round_logs(tmp_path, 300)

    outcome = prune_rounds_dir(tmp_path, keep=400)

    assert outcome.deleted == 0
    assert outcome.deferred == 0
    assert outcome.existing == 300
    assert len(list(tmp_path.glob("R*.log"))) == 300


def test_given_two_digit_rounds_when_prune_rounds_dir_then_sorted_numerically(
    tmp_path: Path,
) -> None:
    """prune_rounds_dir orders by round number, not lexicographically (R9 < R10)."""
    for i in (8, 9, 10, 11):
        (tmp_path / f"R{i}-20260101T000000.log").write_text(f"r{i}")

    outcome = prune_rounds_dir(tmp_path, keep=2)

    assert outcome.deleted == 2
    assert not (tmp_path / "R8-20260101T000000.log").exists()
    assert not (tmp_path / "R9-20260101T000000.log").exists()
    assert (tmp_path / "R10-20260101T000000.log").exists()
    assert (tmp_path / "R11-20260101T000000.log").exists()


def test_given_unrelated_files_when_prune_rounds_dir_then_left_alone(tmp_path: Path) -> None:
    """Filenames that don't match R<n>-*.log are never pruned nor counted."""
    _write_agent_round_logs(tmp_path, 3)
    (tmp_path / "notes.txt").write_text("keep me")
    (tmp_path / "Rx-20260101T000000.log").write_text("keep me")

    outcome = prune_rounds_dir(tmp_path, keep=2)

    assert outcome.deleted == 1
    assert outcome.existing == 3
    assert not (tmp_path / "R1-20260101T000000.log").exists()
    assert (tmp_path / "R3-20260101T000000.log").exists()
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "Rx-20260101T000000.log").exists()


def test_given_missing_rounds_dir_when_prune_rounds_dir_then_no_op(tmp_path: Path) -> None:
    """A rounds/ dir that doesn't exist yet is a no-op, not a crash."""
    outcome = prune_rounds_dir(tmp_path / "rounds", keep=5)

    assert (outcome.deleted, outcome.deferred, outcome.existing) == (0, 0, 0)


def test_given_fewer_files_than_keep_when_prune_rounds_dir_then_nothing_deleted(
    tmp_path: Path,
) -> None:
    """keep larger than the file count deletes nothing."""
    _write_agent_round_logs(tmp_path, 2)

    outcome = prune_rounds_dir(tmp_path, keep=10)

    assert outcome.deleted == 0
    assert outcome.deferred == 0
    assert (tmp_path / "R1-20260101T000000.log").exists()
    assert (tmp_path / "R2-20260101T000000.log").exists()


def test_given_keep_three_when_prune_rounds_dir_then_newest_rounds_survive(tmp_path: Path) -> None:
    """The newest rounds are never pruned — pins that the live round's log survives."""
    _write_agent_round_logs(tmp_path, 5)

    prune_rounds_dir(tmp_path, keep=3)

    assert (tmp_path / "R3-20260101T000000.log").exists()
    assert (tmp_path / "R4-20260101T000000.log").exists()
    assert (tmp_path / "R5-20260101T000000.log").exists()
