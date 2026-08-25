import json

from agent_runner import api, events


def _events(log_dir):
    out = []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        for line in f.read_text().splitlines():
            out.append(json.loads(line))
    return out


def test_emit_schedule_paused_and_resumed(tmp_path):
    api.emit_schedule_paused(
        tmp_path,
        active_window="09:00-12:00",
        resume_at="2026-08-22T12:00:00+08:00",
        timezone="Asia/Shanghai",
    )
    api.emit_schedule_resumed(tmp_path, paused_for_s=7200)
    evs = _events(tmp_path)
    assert [e["event"] for e in evs] == [events.SCHEDULE_PAUSED, events.SCHEDULE_RESUMED]
    assert evs[0]["active_window"] == "09:00-12:00"
    assert evs[0]["resume_at"] == "2026-08-22T12:00:00+08:00"
    assert evs[0]["timezone"] == "Asia/Shanghai"
    assert evs[1]["paused_for_s"] == 7200


def test_schedule_kinds_are_builtin():
    assert events.SCHEDULE_PAUSED in events._BUILTIN_KINDS
    assert events.SCHEDULE_RESUMED in events._BUILTIN_KINDS
