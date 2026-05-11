from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner.events import KNOWN_EVENT_KINDS, emit


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_given_known_event_kind_when_emit_then_writes_json_line(
    tmp_log_dir: Path,
) -> None:
    emit(tmp_log_dir, "round_start", round_num=1)
    files = list(tmp_log_dir.glob("events-*.jsonl"))
    assert len(files) == 1
    rows = _read_jsonl(files[0])
    assert len(rows) == 1
    assert rows[0]["event"] == "round_start"
    assert rows[0]["round_num"] == 1
    assert rows[0]["ts"].endswith("Z")


def test_given_unknown_event_kind_when_emit_then_raises_value_error(
    tmp_log_dir: Path,
) -> None:
    with pytest.raises(ValueError, match="unknown event kind"):
        emit(tmp_log_dir, "made_up_event_xyz", round_num=1)


def test_given_two_emits_in_same_month_when_called_then_appends_to_one_file(
    tmp_log_dir: Path,
) -> None:
    emit(tmp_log_dir, "round_start", round_num=1)
    emit(tmp_log_dir, "round_end", round_num=1)
    files = list(tmp_log_dir.glob("events-*.jsonl"))
    assert len(files) == 1
    assert len(_read_jsonl(files[0])) == 2


def test_given_two_emits_in_different_months_when_called_then_writes_to_separate_files(
    tmp_log_dir: Path,
) -> None:
    april = datetime(2026, 4, 30, 23, 0, tzinfo=UTC)
    may = datetime(2026, 5, 1, 1, 0, tzinfo=UTC)
    with patch("agent_runner.events.datetime") as m:
        m.now.return_value = april
        emit(tmp_log_dir, "round_start", round_num=1)
    with patch("agent_runner.events.datetime") as m:
        m.now.return_value = may
        emit(tmp_log_dir, "round_start", round_num=2)
    assert (tmp_log_dir / "events-2026-04.jsonl").exists()
    assert (tmp_log_dir / "events-2026-05.jsonl").exists()


def test_given_event_kinds_set_when_inspected_then_contains_all_lifecycle_events() -> None:
    expected = {
        "round_start",
        "agent_spawn",
        "agent_exit",
        "dirty_detected",
        "orphan_stashed",
        "orphan_idempotent_skip",
        "orphan_stash_failed",
        "round_timeout_kill",
        "sigterm_received",
        "status_recovered",
        "smoke_check_failed",
        "round_end",
        "monitor_alert_emitted",
        "monitor_auto_stop_triggered",
    }
    assert expected.issubset(KNOWN_EVENT_KINDS)
