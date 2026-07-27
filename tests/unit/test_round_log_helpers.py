"""Tests for agent_runner.round_log helper functions."""

from __future__ import annotations

from pathlib import Path

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


def test_given_many_round_files_when_prune_then_only_recent_kept(tmp_path: Path) -> None:
    """prune_old_round_logs keeps most-recent N by mtime."""
    import os

    for i in range(1, 6):
        path = tmp_path / f"round-{i}.log"
        path.write_text(f"r{i}")
        os.utime(path, (1000000.0 + i, 1000000.0 + i))

    prune_old_round_logs(tmp_path, retention=2)

    # rounds 4, 5 (most recent by mtime) survive; 1, 2, 3 pruned
    assert not (tmp_path / "round-1.log").exists()
    assert not (tmp_path / "round-2.log").exists()
    assert not (tmp_path / "round-3.log").exists()
    assert (tmp_path / "round-4.log").exists()
    assert (tmp_path / "round-5.log").exists()


def test_given_symlink_when_prune_then_symlink_excluded(tmp_path: Path) -> None:
    """The round-current.log symlink is not counted toward retention nor pruned."""
    import os

    for i in range(1, 4):
        path = tmp_path / f"round-{i}.log"
        path.write_text(f"r{i}")
        os.utime(path, (1000000.0 + i, 1000000.0 + i))
    # Create symlink to round-3 (newest)
    atomic_relink(tmp_path / ROUND_CURRENT_LINK, tmp_path / "round-3.log")

    prune_old_round_logs(tmp_path, retention=1)

    # round-3 (most recent) kept
    assert (tmp_path / "round-3.log").exists()
    # symlink intact
    assert (tmp_path / ROUND_CURRENT_LINK).is_symlink()
    # rounds 1, 2 pruned
    assert not (tmp_path / "round-1.log").exists()
    assert not (tmp_path / "round-2.log").exists()


def test_given_two_digit_rounds_when_prune_rounds_dir_then_sorted_numerically(
    tmp_path: Path,
) -> None:
    """prune_rounds_dir orders by round number, not lexicographically (R9 < R10)."""
    for i in (8, 9, 10, 11):
        (tmp_path / f"R{i}-20260101T000000.log").write_text(f"r{i}")

    deleted = prune_rounds_dir(tmp_path, keep=2)

    assert deleted == 2
    assert not (tmp_path / "R8-20260101T000000.log").exists()
    assert not (tmp_path / "R9-20260101T000000.log").exists()
    assert (tmp_path / "R10-20260101T000000.log").exists()
    assert (tmp_path / "R11-20260101T000000.log").exists()


def test_given_unrelated_files_when_prune_rounds_dir_then_left_alone(tmp_path: Path) -> None:
    """Filenames that don't match R<n>-*.log are never pruned."""
    for i in (1, 2, 3):
        (tmp_path / f"R{i}-20260101T000000.log").write_text(f"r{i}")
    (tmp_path / "notes.txt").write_text("keep me")
    (tmp_path / "Rx-20260101T000000.log").write_text("keep me")

    deleted = prune_rounds_dir(tmp_path, keep=1)

    assert deleted == 2
    assert (tmp_path / "R3-20260101T000000.log").exists()
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "Rx-20260101T000000.log").exists()


def test_given_missing_rounds_dir_when_prune_rounds_dir_then_no_op(tmp_path: Path) -> None:
    """A rounds/ dir that doesn't exist yet is a no-op, not a crash."""
    assert prune_rounds_dir(tmp_path / "rounds", keep=5) == 0


def test_given_fewer_files_than_keep_when_prune_rounds_dir_then_nothing_deleted(
    tmp_path: Path,
) -> None:
    """keep larger than the file count deletes nothing."""
    for i in (1, 2):
        (tmp_path / f"R{i}-20260101T000000.log").write_text(f"r{i}")

    assert prune_rounds_dir(tmp_path, keep=10) == 0
    assert (tmp_path / "R1-20260101T000000.log").exists()
    assert (tmp_path / "R2-20260101T000000.log").exists()


def test_given_keep_two_when_prune_rounds_dir_then_newest_rounds_survive(tmp_path: Path) -> None:
    """The newest rounds are never pruned — pins that the live round's log survives."""
    for i in range(1, 6):
        (tmp_path / f"R{i}-20260101T000000.log").write_text(f"r{i}")

    prune_rounds_dir(tmp_path, keep=2)

    assert (tmp_path / "R4-20260101T000000.log").exists()
    assert (tmp_path / "R5-20260101T000000.log").exists()
