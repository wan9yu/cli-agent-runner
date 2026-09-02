"""read_json / read_status / read_orphan_state byte-and-shape hardening
(0.2.13 Group D): a non-UTF-8 byte (SD-card bit-rot on the Pi targets) or a
directory where a state file is expected must degrade to ``None`` -- the
same signal as "no state yet" -- rather than crash serve's loop-top / round
/ peek / monitor / HTTP."""

from __future__ import annotations

from pathlib import Path

from agent_runner.context_store import (
    ORPHAN_FILE,
    STATUS_FILE,
    read_json,
    read_orphan_state,
    read_status,
)


def test_given_non_utf8_bytes_when_read_json_then_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_bytes(b"\xff\xfe not valid utf-8 { ")
    assert read_json(p) is None


def test_given_directory_when_read_json_then_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.mkdir()
    assert read_json(p) is None


def test_given_non_utf8_status_when_read_status_then_returns_none(tmp_log_dir: Path) -> None:
    (tmp_log_dir / STATUS_FILE).write_bytes(b"\xff\xfe\x00")
    assert read_status(tmp_log_dir) is None


def test_given_status_dir_when_read_status_then_returns_none(tmp_log_dir: Path) -> None:
    (tmp_log_dir / STATUS_FILE).mkdir()
    assert read_status(tmp_log_dir) is None


def test_given_string_round_num_when_read_status_then_returns_none(tmp_log_dir: Path) -> None:
    """round_num must type-check -- a corrupt/foreign value must not silently
    become a wrongly-typed Status that later arithmetic (round_num + 1) trips
    on."""
    import json

    (tmp_log_dir / STATUS_FILE).write_text(
        json.dumps({"round_num": "not-a-number", "running": False}), encoding="utf-8"
    )
    assert read_status(tmp_log_dir) is None


def test_given_non_utf8_orphan_when_read_orphan_state_then_returns_none(
    tmp_log_dir: Path,
) -> None:
    (tmp_log_dir / ORPHAN_FILE).write_bytes(b"\xff\xfe\x00")
    assert read_orphan_state(tmp_log_dir) is None


def test_given_orphan_dir_when_read_orphan_state_then_returns_none(tmp_log_dir: Path) -> None:
    (tmp_log_dir / ORPHAN_FILE).mkdir()
    assert read_orphan_state(tmp_log_dir) is None


def test_given_orphan_state_with_unknown_key_when_read_then_known_fields_survive(
    tmp_log_dir: Path,
) -> None:
    import json

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
