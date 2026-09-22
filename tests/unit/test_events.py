from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import events
from agent_runner.events import KNOWN_EVENT_KINDS, emit, register_plugin_kind


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _restore_plugin_kinds():
    """Hermetic isolation: ``_PLUGIN_KINDS`` is a module-global. Without a
    snapshot+restore, a registration in one test leaks into KNOWN_EVENT_KINDS
    for every later test (and flips docgen/count SSOTs + the monitor-remote
    relay's ``sorted(KNOWN_EVENT_KINDS)`` pin).
    """
    snapshot = set(events._PLUGIN_KINDS)
    yield
    events._PLUGIN_KINDS.clear()
    events._PLUGIN_KINDS.update(snapshot)


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
    kind = "made_up_event_xyz"

    with pytest.raises(ValueError) as caught:
        emit(tmp_log_dir, kind, round_num=1)

    actual = str(caught.value)
    assert "unknown event kind" in actual


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


def test_known_event_kinds_should_contain_all_lifecycle_events_when_invoked() -> None:

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

    missing = expected - set(KNOWN_EVENT_KINDS)

    assert not missing


def test_known_event_kinds_should_yield_builtins_when_iterated() -> None:

    out = set(events.KNOWN_EVENT_KINDS)

    actual = "round_start"

    assert actual in out


def test_known_event_kinds_should_support_contains_for_builtins_when_invoked() -> None:

    assert "round_start" in events.KNOWN_EVENT_KINDS

    actual = "nonexistent"

    assert actual not in events.KNOWN_EVENT_KINDS


def test_builtin_kinds_should_include_hook_failed_when_invoked() -> None:
    """Used by runner to surface plugin hook exceptions without crashing."""

    actual = "hook_failed"

    expected = events._BUILTIN_KINDS

    assert actual in expected


def test_builtin_kinds_should_include_monitor_started_when_invoked() -> None:
    from agent_runner.events import _BUILTIN_KINDS, KNOWN_EVENT_KINDS

    assert "monitor_started" in _BUILTIN_KINDS

    assert "monitor_started" in KNOWN_EVENT_KINDS


def test_builtin_kinds_should_include_cgroup_growth_rate_warning_when_invoked() -> None:
    """The mid-round growth-rate detector's WARNING-crossing event."""

    actual = "cgroup_growth_rate_warning"

    expected = events._BUILTIN_KINDS

    assert actual in expected


def test_emit_cgroup_growth_rate_warning_should_write_structured_payload_when_invoked(tmp_path):
    from agent_runner._emit import emit_cgroup_growth_rate_warning
    from agent_runner.events import CGROUP_GROWTH_RATE_WARNING

    emit_cgroup_growth_rate_warning(
        tmp_path,
        round_num=3,
        rate_mb_per_min=900.0,
        threshold_mb_per_min=512.0,
        source="cgroup",
        context={"rate_mb_per_min": 900.0, "threshold_mb_per_min": 512.0},
    )

    payload = _read_jsonl(sorted(tmp_path.glob("events-*.jsonl"))[-1])[-1]
    assert payload["event"] == CGROUP_GROWTH_RATE_WARNING
    assert payload["round_num"] == 3
    assert payload["rate_mb_per_min"] == 900.0
    assert payload["threshold_mb_per_min"] == 512.0
    assert payload["source"] == "cgroup"
    assert payload["context"] == {"rate_mb_per_min": 900.0, "threshold_mb_per_min": 512.0}


def test_emit_agent_auth_error_detected_should_write_structured_payload_when_invoked(tmp_path):
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


def test_emit_agent_auth_error_detected_should_redact_secrets_in_raw_when_invoked(tmp_path):
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


def test_emit_round_logs_prune_deferred_should_write_actionable_payload_when_invoked(tmp_path):
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


def test_emit_transient_error_backoff_capped_should_include_extended_payload_when_invoked(tmp_path):
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


def test_emit_transient_error_backoff_capped_should_omit_new_fields_with_old_signature_when_invoked(
    tmp_path,
):
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


def test_register_plugin_kind_should_allow_emit_when_name_is_namespaced(
    tmp_log_dir: Path,
) -> None:
    register_plugin_kind("myplugin_ok")

    emit(tmp_log_dir, "myplugin_ok", round_num=1)

    files = list(tmp_log_dir.glob("events-*.jsonl"))
    rows = _read_jsonl(files[0])
    assert rows[0]["event"] == "myplugin_ok"


def test_emit_should_still_raise_value_error_when_kind_is_unregistered(
    tmp_log_dir: Path,
) -> None:

    kind = "typo_unregistered"

    with pytest.raises(ValueError) as caught:
        emit(tmp_log_dir, kind)

    actual = str(caught.value)
    assert "unknown event kind" in actual


def test_register_plugin_kind_should_raise_when_name_collides_with_builtin() -> None:

    kind = "round_start"

    with pytest.raises(ValueError) as caught:
        register_plugin_kind(kind)

    actual = str(caught.value)
    assert "collides with a builtin kind" in actual


def test_register_plugin_kind_should_raise_when_first_segment_is_builtin_prefix() -> None:

    kind = "round_myplugin"

    with pytest.raises(ValueError) as caught:
        register_plugin_kind(kind)

    actual = str(caught.value)
    assert "builtin-owned prefix" in actual


def test_register_plugin_kind_should_raise_when_name_has_no_underscore() -> None:

    kind = "plain"

    with pytest.raises(ValueError) as caught:
        register_plugin_kind(kind)

    actual = str(caught.value)
    assert "namespaced" in actual


def test_register_plugin_kind_should_grow_known_event_kinds_when_registered() -> None:
    before = len(KNOWN_EVENT_KINDS)

    register_plugin_kind("myplugin_grew")

    assert "myplugin_grew" in KNOWN_EVENT_KINDS
    assert len(KNOWN_EVENT_KINDS) == before + 1
