from __future__ import annotations

import json
from pathlib import Path

from agent_runner.context_store import (
    STATUS_FILE,
    OrphanState,
    Status,
    atomic_write_json,
    read_orphan_state,
    read_status,
    write_orphan_state,
    write_status,
)


def test_atomic_write_json_should_leave_no_tmp_file_when_writing(tmp_path: Path) -> None:
    p = tmp_path / "x.json"

    atomic_write_json(p, {"k": "v"})

    assert json.loads(p.read_text()) == {"k": "v"}
    assert not (tmp_path / "x.json.tmp").exists()


def test_status_should_round_trip_when_written_and_read(tmp_log_dir: Path) -> None:
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


def test_read_status_should_return_none_when_no_status_file(tmp_log_dir: Path) -> None:
    assert read_status(tmp_log_dir) is None


def test_read_status_should_return_none_when_status_corrupt(tmp_log_dir: Path) -> None:
    (tmp_log_dir / STATUS_FILE).write_text("not json {")

    result = read_status(tmp_log_dir)

    assert result is None


def test_read_status_should_keep_known_fields_when_unknown_key_present(
    tmp_log_dir: Path,
) -> None:
    (tmp_log_dir / STATUS_FILE).write_text(
        json.dumps({"round_num": 9, "running": False, "future_field": "x"}),
        encoding="utf-8",
    )

    s = read_status(tmp_log_dir)

    assert s is not None and s.round_num == 9


def test_orphan_state_should_round_trip_when_written_and_read(
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
