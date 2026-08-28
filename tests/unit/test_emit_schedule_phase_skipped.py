"""emit_schedule_phase_skipped wrapper + catalog membership (0.2.9)."""

from __future__ import annotations

import json

from agent_runner import events
from agent_runner._emit import emit_schedule_phase_skipped


def _events(log_dir):
    out = []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        out += [json.loads(x) for x in f.read_text().splitlines()]
    return out


def test_kind_is_registered_builtin():
    assert events.SCHEDULE_PHASE_SKIPPED == "schedule_phase_skipped"
    assert "schedule_phase_skipped" in events.KNOWN_EVENT_KINDS
    assert "schedule_phase_skipped" in events._BUILTIN_KINDS


def test_emit_writes_payload(tmp_path):
    emit_schedule_phase_skipped(
        tmp_path, round_num=5, skipped=["a", "b"], chosen="c", active_window="09:00-12:00"
    )
    (evt,) = _events(tmp_path)
    assert evt["event"] == "schedule_phase_skipped"
    assert evt["round_num"] == 5
    assert evt["skipped"] == ["a", "b"]
    assert evt["chosen"] == "c"
    assert evt["active_window"] == "09:00-12:00"


def test_reexported_from_api():
    from agent_runner import api

    assert api.emit_schedule_phase_skipped is emit_schedule_phase_skipped
