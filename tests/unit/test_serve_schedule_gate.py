import json
import types
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_runner import config, schedule
from agent_runner.cli import serve_cmd


def _cfg(pause):
    return config.ScheduleConfig(
        timezone="Asia/Shanghai",
        pause_windows=tuple(schedule.parse_window(w) for w in pause),
    )


def _mk_cfg(tmp_path, schedule_cfg):
    return types.SimpleNamespace(schedule=schedule_cfg)


class _FakeClock:
    def __init__(self, hours):
        self._hours = list(hours)

    def __call__(self, _tz):
        h = self._hours.pop(0)
        return datetime(2026, 8, 22, h, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _events(log_dir):
    out = []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        out += [json.loads(x) for x in f.read_text().splitlines()]
    return out


def test_gate_no_pause_when_runnable(tmp_path):
    cfg = _mk_cfg(tmp_path, _cfg(["09:00-12:00"]))
    stop = {"requested": False}
    paused = serve_cmd._maybe_pause_for_schedule(
        cfg,
        tmp_path,
        stop,
        now_fn=lambda _tz: datetime(2026, 8, 22, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        sleep_fn=lambda _s: None,
    )
    assert paused is False
    assert _events(tmp_path) == []


def test_gate_pauses_then_resumes(tmp_path):
    cfg = _mk_cfg(tmp_path, _cfg(["09:00-12:00"]))
    stop = {"requested": False}
    slept = []
    clock = _FakeClock([10, 10, 12])  # enter@10, still paused@10, runnable@12
    paused = serve_cmd._maybe_pause_for_schedule(
        cfg, tmp_path, stop, now_fn=clock, sleep_fn=lambda s: slept.append(s), chunk_s=30
    )
    assert paused is True
    evs = [e["event"] for e in _events(tmp_path)]
    assert evs == ["schedule_paused", "schedule_resumed"]
    assert slept  # at least one chunk slept


def test_gate_interrupted_by_stop(tmp_path):
    cfg = _mk_cfg(tmp_path, _cfg(["09:00-12:00"]))
    stop = {"requested": False}

    def _sleep(_s):
        stop["requested"] = True

    paused = serve_cmd._maybe_pause_for_schedule(
        cfg,
        tmp_path,
        stop,
        now_fn=lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        sleep_fn=_sleep,
    )
    assert paused is True
    assert [e["event"] for e in _events(tmp_path)] == ["schedule_paused"]
