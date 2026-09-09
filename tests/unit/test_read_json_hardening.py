"""read_json / read_status / read_orphan_state byte-and-shape hardening
(0.2.13 Group D): a non-UTF-8 byte (SD-card bit-rot on the Pi targets) or a
directory where a state file is expected must degrade to ``None`` -- the
same signal as "no state yet" -- rather than crash serve's loop-top / round
/ peek / monitor / HTTP."""

from __future__ import annotations

import json
from pathlib import Path

from agent_runner.context_store import (
    ORPHAN_FILE,
    STATUS_FILE,
    read_json,
    read_orphan_state,
    read_status,
)


def test_read_json_should_return_none_when_bytes_are_not_utf8(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_bytes(b"\xff\xfe not valid utf-8 { ")

    assert read_json(p) is None


def test_read_json_should_return_none_when_path_is_a_directory(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.mkdir()

    assert read_json(p) is None


def test_read_status_should_return_none_when_status_file_is_not_utf8(tmp_log_dir: Path) -> None:
    (tmp_log_dir / STATUS_FILE).write_bytes(b"\xff\xfe\x00")

    assert read_status(tmp_log_dir) is None


def test_read_status_should_return_none_when_status_file_is_a_directory(
    tmp_log_dir: Path,
) -> None:
    (tmp_log_dir / STATUS_FILE).mkdir()

    assert read_status(tmp_log_dir) is None


def test_read_status_should_return_none_when_round_num_is_a_string(tmp_log_dir: Path) -> None:
    """round_num must type-check -- a corrupt/foreign value must not silently
    become a wrongly-typed Status that later arithmetic (round_num + 1) trips
    on."""
    (tmp_log_dir / STATUS_FILE).write_text(
        json.dumps({"round_num": "not-a-number", "running": False}), encoding="utf-8"
    )

    assert read_status(tmp_log_dir) is None


def test_read_orphan_state_should_return_none_when_file_is_not_utf8(tmp_log_dir: Path) -> None:
    (tmp_log_dir / ORPHAN_FILE).write_bytes(b"\xff\xfe\x00")

    assert read_orphan_state(tmp_log_dir) is None


def test_read_orphan_state_should_return_none_when_path_is_a_directory(
    tmp_log_dir: Path,
) -> None:
    (tmp_log_dir / ORPHAN_FILE).mkdir()

    assert read_orphan_state(tmp_log_dir) is None


def test_read_orphan_state_should_keep_known_fields_when_unknown_key_present(
    tmp_log_dir: Path,
) -> None:
    (tmp_log_dir / ORPHAN_FILE).write_text(
        json.dumps(
            {
                "round_num": 7,
                "files": ["a.py"],
                "stashed_ref": "stash@{0}",
                "stash_message": "ORPHAN R7",
                "timestamp": "2026-05-11T15:25:30.000Z",
                "future_field": "x",
            }
        ),
        encoding="utf-8",
    )

    state = read_orphan_state(tmp_log_dir)

    assert state is not None and state.round_num == 7
