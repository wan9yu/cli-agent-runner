from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import events
from agent_runner.events import KNOWN_EVENT_KINDS, emit, read_new
from tests._test_helpers import isolating

_reset = isolating(events._PLUGIN_KINDS)


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_emit_should_write_json_line_when_kind_is_known(tmp_log_dir: Path) -> None:
    emit(tmp_log_dir, "round_start", round_num=1)

    files = list(tmp_log_dir.glob("events-*.jsonl"))
    rows = _read_jsonl(files[0])

    assert len(files) == 1
    assert len(rows) == 1
    assert rows[0]["event"] == "round_start"
    assert rows[0]["round_num"] == 1
    assert rows[0]["ts"].endswith("Z")


def test_emit_should_raise_value_error_when_kind_is_unknown(tmp_log_dir: Path) -> None:
    with pytest.raises(ValueError, match="unknown event kind"):
        emit(tmp_log_dir, "made_up_event_xyz", round_num=1)


def test_emit_should_append_to_one_file_when_two_emits_in_same_month(tmp_log_dir: Path) -> None:
    emit(tmp_log_dir, "round_start", round_num=1)
    emit(tmp_log_dir, "round_end", round_num=1)

    files = list(tmp_log_dir.glob("events-*.jsonl"))

    assert len(files) == 1
    assert len(_read_jsonl(files[0])) == 2


def test_emit_should_write_separate_files_when_emits_span_different_months(
    tmp_log_dir: Path,
) -> None:
    april = datetime(2026, 4, 30, 23, 0, tzinfo=UTC)
    may = datetime(2026, 5, 1, 1, 0, tzinfo=UTC)

    with patch("agent_runner.clock.SYSTEM_CLOCK.now_utc", return_value=april):
        emit(tmp_log_dir, "round_start", round_num=1)
    with patch("agent_runner.clock.SYSTEM_CLOCK.now_utc", return_value=may):
        emit(tmp_log_dir, "round_start", round_num=2)

    assert (tmp_log_dir / "events-2026-04.jsonl").exists()
    assert (tmp_log_dir / "events-2026-05.jsonl").exists()


def test_known_event_kinds_should_contain_all_lifecycle_events() -> None:
    expected = {
        "round_start",
        "agent_spawn",
        "agent_exit",
        "dirty_detected",
        "orphan_stashed",
        "orphan_idempotent_skip",
        "orphan_stash_failed",
        "round_timeout_kill",
        "status_recovered",
        "smoke_check_failed",
        "round_end",
        "monitor_alert_emitted",
        "monitor_auto_stop_triggered",
    }

    assert expected.issubset(KNOWN_EVENT_KINDS)


def test_phase_window_overlap_should_be_a_registered_builtin_kind() -> None:
    assert "phase_window_overlap" in KNOWN_EVENT_KINDS


def test_registered_plugin_kind_should_appear_in_known_kinds() -> None:
    events.register_event_kind("custom_test_kind", source="test-plugin")

    assert "custom_test_kind" in events.KNOWN_EVENT_KINDS


def test_register_event_kind_should_raise_when_re_registering_a_builtin() -> None:
    with pytest.raises(ValueError, match="built-in"):
        events.register_event_kind("round_start", source="some-plugin")


def test_register_event_kind_should_be_idempotent_when_same_kind_same_source() -> None:
    events.register_event_kind("dup_kind", source="plug-a")
    events.register_event_kind("dup_kind", source="plug-a")  # no raise

    assert "dup_kind" in events.KNOWN_EVENT_KINDS


def test_register_event_kind_should_raise_when_same_kind_different_source() -> None:
    events.register_event_kind("conflict_kind", source="plug-a")

    with pytest.raises(ValueError, match="already registered"):
        events.register_event_kind("conflict_kind", source="plug-b")


def test_emit_should_not_raise_when_kind_is_plugin_registered(tmp_path) -> None:
    events.register_event_kind("plugin_emit_test", source="test")

    events.emit(tmp_path, "plugin_emit_test", note="hello")

    assert any(tmp_path.glob("events-*.jsonl"))


def test_plugin_event_kinds_should_return_a_sorted_list() -> None:
    events.register_event_kind("z_late_kind", source="t")
    events.register_event_kind("a_early_kind", source="t")

    assert events.plugin_event_kinds() == ["a_early_kind", "z_late_kind"]


def test_plugin_event_kinds_should_return_empty_list_when_none_registered() -> None:
    assert events.plugin_event_kinds() == []


def test_known_event_kinds_should_yield_builtins_and_plugins_when_iterated() -> None:
    events.register_event_kind("plug_x", source="t")

    out = set(events.KNOWN_EVENT_KINDS)

    assert "round_start" in out  # built-in
    assert "plug_x" in out  # plugin


def test_known_event_kinds_should_support_contains_for_builtins_and_plugins() -> None:
    events.register_event_kind("plug_y", source="t")

    assert "round_start" in events.KNOWN_EVENT_KINDS
    assert "plug_y" in events.KNOWN_EVENT_KINDS
    assert "nonexistent" not in events.KNOWN_EVENT_KINDS


def test_builtin_kinds_should_include_hook_failed() -> None:
    """Used by runner to surface plugin hook exceptions without crashing."""
    assert "hook_failed" in events._BUILTIN_KINDS


def test_builtin_kinds_should_include_monitor_started() -> None:
    from agent_runner.events import _BUILTIN_KINDS, KNOWN_EVENT_KINDS

    assert "monitor_started" in _BUILTIN_KINDS
    assert "monitor_started" in KNOWN_EVENT_KINDS


def test_emit_agent_auth_error_detected_should_write_structured_payload(tmp_path):
    """The agent's own output reported an auth failure — certain evidence, so the
    monitor's oauth_fail detector counts the round without an exit-code shield.
    """
    from agent_runner._emit import emit_agent_auth_error_detected
    from agent_runner.events import AGENT_AUTH_ERROR_DETECTED

    emit_agent_auth_error_detected(
        tmp_path,
        round_num=7,
        agent="pi",
        raw='401: {"message":"Invalid Authentication"}',
    )

    line = sorted(tmp_path.glob("events-*.jsonl"))[-1].read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["event"] == AGENT_AUTH_ERROR_DETECTED
    assert payload["round_num"] == 7
    assert payload["agent"] == "pi"
    assert "401" in payload["raw"]


def test_emit_agent_auth_error_detected_should_redact_secrets_in_raw(tmp_path):
    """raw is a provider error body — the same redaction the transient sibling applies."""
    from agent_runner._emit import emit_agent_auth_error_detected

    emit_agent_auth_error_detected(
        tmp_path,
        round_num=1,
        agent="pi",
        raw='401: {"key":"sk-ant-abcdefghijklmnopqrstuvwxyz0123456789"}',
    )

    line = sorted(tmp_path.glob("events-*.jsonl"))[-1].read_text(encoding="utf-8").strip()
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789" not in json.loads(line)["raw"]


def test_emit_round_logs_prune_deferred_should_write_actionable_payload(tmp_path):
    """The deferral must be actionable from the event alone: which directory,
    how many files exist, what retention is set to, how many were spared, and
    the knob to turn.
    """
    from agent_runner._emit import emit_round_logs_prune_deferred
    from agent_runner.events import ROUND_LOGS_PRUNE_DEFERRED

    emit_round_logs_prune_deferred(
        tmp_path,
        directory=str(tmp_path / "rounds"),
        existing=12293,
        keep=100,
        would_delete=12193,
    )

    line = sorted(tmp_path.glob("events-*.jsonl"))[-1].read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["event"] == ROUND_LOGS_PRUNE_DEFERRED
    assert payload["directory"] == str(tmp_path / "rounds")
    assert payload["existing"] == 12293
    assert payload["keep"] == 100
    assert payload["would_delete"] == 12193
    assert "runtime.round_log_retention" in payload["hint"]
    assert "12293" in payload["hint"]


def test_emit_transient_error_backoff_capped_should_include_extended_payload(tmp_path):
    """0.1.33+ payload includes original_reset_at_epoch, applied_reset_at_epoch,
    consecutive_count, capped_by_absolute_max for backoff-curve observability.
    """
    from agent_runner._emit import emit_transient_error_backoff_capped

    emit_transient_error_backoff_capped(
        tmp_path,
        classification="rate_limit_model",
        agent="claude",
        requested_sleep_s=120,
        applied_sleep_s=120,
        original_reset_at_epoch=1715990000,
        applied_reset_at_epoch=1715990120,
        consecutive_count=2,
        capped_by_absolute_max=False,
    )

    events_files = sorted(tmp_path.glob("events-*.jsonl"))
    assert events_files, "no events file emitted"
    line = events_files[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "transient_error_backoff_capped"
    assert payload["classification"] == "rate_limit_model"
    assert payload["agent"] == "claude"
    assert payload["requested_sleep_s"] == 120
    assert payload["applied_sleep_s"] == 120
    assert payload["original_reset_at_epoch"] == 1715990000
    assert payload["applied_reset_at_epoch"] == 1715990120
    assert payload["consecutive_count"] == 2
    assert payload["capped_by_absolute_max"] is False


def test_emit_transient_error_backoff_capped_should_omit_new_fields_with_old_signature(tmp_path):
    """Old call sites (only 4 kwargs) still work; new fields absent in payload.
    Guards against breaking existing _throttle.py:_apply_back_off behavior.
    """
    from agent_runner._emit import emit_transient_error_backoff_capped

    emit_transient_error_backoff_capped(
        tmp_path,
        classification="rate_limit_model",
        agent="claude",
        requested_sleep_s=120,
        applied_sleep_s=60,
    )

    events_files = sorted(tmp_path.glob("events-*.jsonl"))
    line = events_files[-1].read_text(encoding="utf-8").strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["classification"] == "rate_limit_model"
    assert payload["requested_sleep_s"] == 120
    assert payload["applied_sleep_s"] == 60
    # New fields absent (not in payload at all, since defaults are None and we
    # skip emitting None-valued kwargs).
    assert "original_reset_at_epoch" not in payload
    assert "consecutive_count" not in payload


def test_read_new_should_return_all_rows_when_first_read(tmp_path: Path) -> None:
    old = tmp_path / "events-2026-08.jsonl"
    old.write_text(json.dumps({"event": "round_start", "n": 1}) + "\n")

    events_read, offsets = read_new([old], {})

    assert [e["n"] for e in events_read] == [1]
    assert offsets[old] == old.stat().st_size  # offset recorded at EOF


def test_read_new_should_return_only_appended_rows_when_file_grows(tmp_path: Path) -> None:
    old = tmp_path / "events-2026-08.jsonl"
    old.write_text(json.dumps({"event": "round_start", "n": 1}) + "\n")
    _, offsets = read_new([old], {})

    with old.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "round_end", "n": 2}) + "\n")
    appended_events, offsets = read_new([old], offsets)

    assert [e["n"] for e in appended_events] == [2]
    assert offsets[old] == old.stat().st_size


def test_read_new_should_read_new_file_from_start_when_rotated_in(tmp_path: Path) -> None:
    old = tmp_path / "events-2026-08.jsonl"
    old.write_text(json.dumps({"event": "round_start", "n": 1}) + "\n")
    _, offsets = read_new([old], {})

    new = tmp_path / "events-2026-09.jsonl"
    new.write_text(json.dumps({"event": "round_start", "n": 3}) + "\n")
    rotated_events, offsets = read_new([old, new], offsets)

    assert [e["n"] for e in rotated_events] == [3]  # new file read from byte 0
    assert offsets[old] == old.stat().st_size  # old untouched: no new bytes


def test_read_new_should_reread_from_start_when_truncated_below_offset(tmp_path: Path) -> None:
    # A 2-line file read once records its offset at the larger size; the state
    # dependency is load-bearing -- the truncation is only "below offset"
    # because the recorded offset is for the taller file.
    old = tmp_path / "events-2026-08.jsonl"
    old.write_text(
        json.dumps({"event": "round_start", "n": 1})
        + "\n"
        + json.dumps({"event": "round_end", "n": 2})
        + "\n"
    )
    _, offsets = read_new([old], {})

    old.write_text(json.dumps({"event": "round_start", "n": 4}) + "\n")
    reset_events, offsets = read_new([old], offsets)

    assert [e["n"] for e in reset_events] == [4]  # re-read from 0, not skipped
    assert offsets[old] == old.stat().st_size
