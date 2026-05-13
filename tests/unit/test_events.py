from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import events
from agent_runner.events import KNOWN_EVENT_KINDS, emit
from tests._test_helpers import isolating

_reset = isolating(events._PLUGIN_KINDS)


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


def test_given_new_plugin_kind_when_registered_then_present_in_known_kinds() -> None:
    events.register_event_kind("custom_test_kind", source="test-plugin")
    assert "custom_test_kind" in events.KNOWN_EVENT_KINDS


def test_given_builtin_kind_when_re_registered_then_raises_value_error() -> None:
    with pytest.raises(ValueError, match="built-in"):
        events.register_event_kind("round_start", source="some-plugin")


def test_given_same_plugin_kind_same_source_when_re_registered_then_idempotent() -> None:
    events.register_event_kind("dup_kind", source="plug-a")
    events.register_event_kind("dup_kind", source="plug-a")  # no raise
    assert "dup_kind" in events.KNOWN_EVENT_KINDS


def test_given_same_plugin_kind_different_source_when_registered_then_raises() -> None:
    events.register_event_kind("conflict_kind", source="plug-a")
    with pytest.raises(ValueError, match="already registered"):
        events.register_event_kind("conflict_kind", source="plug-b")


def test_given_plugin_registered_kind_when_emitted_then_no_validation_error(tmp_path) -> None:
    events.register_event_kind("plugin_emit_test", source="test")
    events.emit(tmp_path, "plugin_emit_test", note="hello")
    assert any(tmp_path.glob("events-*.jsonl"))


def test_given_plugin_kinds_when_plugin_event_kinds_called_then_sorted_list() -> None:
    events.register_event_kind("z_late_kind", source="t")
    events.register_event_kind("a_early_kind", source="t")
    assert events.plugin_event_kinds() == ["a_early_kind", "z_late_kind"]


def test_given_no_plugin_kinds_when_plugin_event_kinds_called_then_empty_list() -> None:
    assert events.plugin_event_kinds() == []


def test_given_known_event_kinds_view_when_iterated_then_yields_builtin_plus_plugins() -> None:
    events.register_event_kind("plug_x", source="t")
    out = set(events.KNOWN_EVENT_KINDS)
    assert "round_start" in out  # built-in
    assert "plug_x" in out  # plugin


def test_given_known_event_kinds_view_when_contains_checked_then_works_for_both() -> None:
    events.register_event_kind("plug_y", source="t")
    assert "round_start" in events.KNOWN_EVENT_KINDS
    assert "plug_y" in events.KNOWN_EVENT_KINDS
    assert "nonexistent" not in events.KNOWN_EVENT_KINDS


def test_given_hook_failed_when_checked_then_in_builtin_kinds() -> None:
    """hook_failed is a built-in event kind in 0.1.4+.

    Used by runner to surface plugin hook exceptions without crashing.
    """
    assert "hook_failed" in events._BUILTIN_KINDS


def test_given_monitor_started_kind_when_imported_then_in_known_event_kinds() -> None:
    """monitor_started is the built-in startup-confirmation event."""
    from agent_runner.events import _BUILTIN_KINDS, KNOWN_EVENT_KINDS

    assert "monitor_started" in _BUILTIN_KINDS
    assert "monitor_started" in KNOWN_EVENT_KINDS
