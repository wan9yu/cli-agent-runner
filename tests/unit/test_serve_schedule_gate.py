import json
import types
from argparse import Namespace
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_runner import config, schedule
from agent_runner.cli import _build_parser, serve_cmd
from tests._test_helpers import make_toml


def _cfg(pause):
    return config.ScheduleConfig(
        timezone="Asia/Shanghai",
        pause_windows=tuple(schedule.parse_window(w) for w in pause),
    )


def _mk_cfg(tmp_path, schedule_cfg, stop_file=None):
    return types.SimpleNamespace(
        schedule=schedule_cfg,
        runtime=types.SimpleNamespace(stop_file=stop_file),
    )


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


def test_gate_breaks_on_stop_file_during_pause(tmp_path):
    """A stop_file dropped during a pause is noticed on the first poll: the loop
    breaks without sleeping and without emitting schedule_resumed (the window did
    not open), leaving the outer serve loop to emit stop_file_detected."""
    stop_path = tmp_path / "STOP"
    stop_path.write_text("halt")
    cfg = _mk_cfg(tmp_path, _cfg(["09:00-12:00"]), stop_file=stop_path)
    stop = {"requested": False}
    slept = []
    paused = serve_cmd._maybe_pause_for_schedule(
        cfg,
        tmp_path,
        stop,
        now_fn=lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        sleep_fn=lambda s: slept.append(s),
    )
    assert paused is True
    assert [e["event"] for e in _events(tmp_path)] == ["schedule_paused"]
    assert slept == []  # stop_file seen on the first iteration, no waiting


def _toml_with_always_pause(tmp_path):
    """A config whose pause window covers every minute of every day, so the
    schedule gate would pause on any clock — isolating the --ignore-schedule
    bypass from the wall clock."""
    cfg_path = make_toml(tmp_path)
    with cfg_path.open("a", encoding="utf-8") as f:
        f.write('[schedule]\npause_windows = ["00:00-24:00"]\n')
    return cfg_path


def test_ignore_schedule_bypasses_gate_and_runs_round(monkeypatch, tmp_path):
    """serve --ignore-schedule runs the round with no schedule_paused emitted,
    even though the configured pause window is active for the entire day."""
    cfg_path = _toml_with_always_pause(tmp_path)
    log_dir = tmp_path / "logs"
    ran = []

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        ran.append(1)
        round_log_path.write_text("")
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    args = Namespace(config=cfg_path, once=True, max_rounds=None, ignore_schedule=True)
    rc = serve_cmd.cmd(args)

    assert rc == 0
    assert ran  # the round subprocess was invoked despite the always-on pause window
    assert "schedule_paused" not in [e["event"] for e in _events(log_dir)]


def test_ignore_schedule_defaults_to_false():
    """Without the flag, args.ignore_schedule is False so the gate stays armed."""
    args = _build_parser().parse_args(["serve", "--config", "/tmp/x.toml"])
    assert args.ignore_schedule is False
    args = _build_parser().parse_args(["serve", "--config", "/tmp/x.toml", "--ignore-schedule"])
    assert args.ignore_schedule is True
