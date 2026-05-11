from __future__ import annotations

import json
from pathlib import Path

from agent_runner.context_store import (
    OrphanState,
    Status,
    atomic_write_json,
    read_orphan_state,
    read_round_context,
    read_status,
    write_orphan_state,
    write_round_context,
    write_status,
)


def test_given_path_when_atomic_write_then_uses_tmp_rename(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    atomic_write_json(p, {"k": "v"})
    assert json.loads(p.read_text()) == {"k": "v"}
    assert not (tmp_path / "x.json.tmp").exists()


def test_given_status_round_trip_when_written_and_read_then_equal(tmp_log_dir: Path) -> None:
    s = Status(
        round_num=42,
        running=False,
        last_completed_at="2026-05-11T15:25:30.000Z",
        last_exit_code=0,
        last_duration_s=412.4,
        current_phase="diverge",
        phase_index=0,
    )
    write_status(tmp_log_dir, s)
    assert read_status(tmp_log_dir) == s


def test_given_no_status_file_when_read_then_returns_none(tmp_log_dir: Path) -> None:
    assert read_status(tmp_log_dir) is None


def test_given_corrupt_status_when_read_then_returns_none(tmp_log_dir: Path) -> None:
    (tmp_log_dir / "status.json").write_text("not json {")
    assert read_status(tmp_log_dir) is None


def test_given_first_round_context_when_written_then_omits_previous_field(
    tmp_log_dir: Path,
) -> None:
    write_round_context(tmp_log_dir, round_num=1, started_at="2026-05-11T15:00:00.000Z")
    ctx = read_round_context(tmp_log_dir)
    assert ctx == {"round_num": 1, "started_at": "2026-05-11T15:00:00.000Z"}


def test_given_phases_unconfigured_when_writing_round_context_then_phase_field_absent(
    tmp_log_dir: Path,
) -> None:
    write_round_context(
        tmp_log_dir, round_num=2, started_at="2026-05-11T15:00:00.000Z", phase=None
    )
    ctx = read_round_context(tmp_log_dir)
    assert "phase" not in ctx


def test_given_orphan_stash_set_when_writing_context_then_includes_ref_msg_files_fields(
    tmp_log_dir: Path,
) -> None:
    write_round_context(
        tmp_log_dir,
        round_num=42,
        started_at="2026-05-11T15:00:00.000Z",
        orphan_stash={
            "ref": "stash@{0}",
            "message": "ORPHAN R41 ts=2026-05-11T15:25:30",
            "files": ["src/foo.py"],
        },
    )
    ctx = read_round_context(tmp_log_dir)
    assert ctx["orphan_stash"]["ref"] == "stash@{0}"
    assert ctx["orphan_stash"]["files"] == ["src/foo.py"]


def test_given_orphan_state_round_trip_when_written_and_read_then_equal(
    tmp_log_dir: Path,
) -> None:
    s = OrphanState(
        round_num=41,
        files=["src/foo.py"],
        stashed_ref="stash@{0}",
        stash_message="ORPHAN R41 ts=...",
        timestamp="2026-05-11T15:25:30.000Z",
        phase=None,
    )
    write_orphan_state(tmp_log_dir, s)
    assert read_orphan_state(tmp_log_dir) == s
