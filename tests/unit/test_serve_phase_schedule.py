"""serve loop per-phase scheduling: --phase passthrough, wait/skip policy, and
the byte-identical legacy path (0.2.9).

Drives serve_cmd.cmd() with subprocess.run mocked to capture the round argv and
an injected clock, mirroring test_serve_schedule_gate.py.
"""

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_runner.cli import serve_cmd
from tests._test_helpers import make_toml_with_sections

TZ = ZoneInfo("Asia/Shanghai")


def _events(log_dir):
    out = []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        out += [json.loads(x) for x in f.read_text().splitlines()]
    return out


def _capture_run(monkeypatch):
    """Patch subprocess.run to record round argvs only (skips git substrate calls);
    returns the list of ``agent_runner.cli ... round`` invocations."""
    argvs: list[list[str]] = []

    def _fake(argv, **_k):
        if "agent_runner.cli" in argv:
            argvs.append(argv)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", _fake)
    return argvs


def _cfg_path(tmp_path, phases_block):
    return make_toml_with_sections(tmp_path, phases_block=phases_block)


def _args(cfg_path):
    return Namespace(config=cfg_path, once=True, max_rounds=None, ignore_schedule=False)


def _phase_of(argv):
    return argv[argv.index("--phase") + 1] if "--phase" in argv else None


# --- phase-aware: --phase appended when a phase is selected ---------------


def test_skip_all_open_appends_rotation_phase(monkeypatch, tmp_path):
    argvs = _capture_run(monkeypatch)
    cfg_path = _cfg_path(tmp_path, '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n')
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    assert len(argvs) == 1
    assert _phase_of(argvs[0]) == "a"


def test_skip_runs_next_and_emits_skip_event(monkeypatch, tmp_path):
    argvs = _capture_run(monkeypatch)
    monkeypatch.setattr(
        serve_cmd.schedule,
        "now_in_zone",
        lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=TZ),
    )
    cfg_path = _cfg_path(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n'
        '[phases.a.schedule]\npause_windows = ["09:00-12:00"]\n',
    )
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    assert _phase_of(argvs[0]) == "b"  # a closed → skipped, b runs
    evs = _events(tmp_path / "logs")
    skip = [e for e in evs if e["event"] == "schedule_phase_skipped"]
    assert skip and skip[0]["skipped"] == ["a"] and skip[0]["chosen"] == "b"


class _FlipClock:
    """Returns a closed hour (10, inside 09:00-12:00 pause) until the first sleep,
    then an open hour (13). Lets a cmd()-driven pause loop advance deterministically
    regardless of how many times the clock is polled."""

    def __init__(self):
        self.opened = False

    def __call__(self, _tz):
        return datetime(2026, 8, 22, 13 if self.opened else 10, 0, tzinfo=TZ)


def test_wait_pauses_on_rotation_window(monkeypatch, tmp_path):
    argvs = _capture_run(monkeypatch)
    clock = _FlipClock()
    monkeypatch.setattr(serve_cmd.schedule, "now_in_zone", clock)
    monkeypatch.setattr(serve_cmd.time, "sleep", lambda _s: setattr(clock, "opened", True))
    cfg_path = _cfg_path(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a"]\nphase_policy = "wait"\n'
        '[phases.a.schedule]\npause_windows = ["09:00-12:00"]\n',
    )
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    evs = [e["event"] for e in _events(tmp_path / "logs")]
    assert "schedule_paused" in evs and "schedule_resumed" in evs
    paused = next(e for e in _events(tmp_path / "logs") if e["event"] == "schedule_paused")
    assert paused["phase"] == "a"  # phase-aware pause carries the phase
    assert paused["resume_at"].startswith("2026-08-22T12:00")  # phase a's next open
    assert _phase_of(argvs[0]) == "a"  # after resume it runs phase a


# --- legacy path (no per-phase schedule, wait policy): byte-identical -----


def test_legacy_phases_no_per_phase_schedule_appends_no_phase(monkeypatch, tmp_path):
    """[phases] present but phase_policy=wait and NO per-phase schedule → not
    phase-aware → legacy pause path, no --phase appended (0.2.7 behavior)."""
    argvs = _capture_run(monkeypatch)
    cfg_path = _cfg_path(tmp_path, '[phases]\nlist = ["a","b"]\n')
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    assert _phase_of(argvs[0]) is None  # round self-resolves rotation


def test_phase_aware_predicate(tmp_path):
    """Only new 0.2.9 syntax (skip policy, or a per-phase schedule) is phase-aware;
    a plain wait rotation and a no-phases config take the legacy path."""
    from agent_runner.config import load_config

    def _pa(block):
        return serve_cmd._phase_aware(load_config(_cfg_path(tmp_path, block)))

    assert _pa('[phases]\nlist = ["a"]\nphase_policy = "skip"\n') is True
    assert (
        _pa('[phases]\nlist = ["a"]\n[phases.a.schedule]\npause_windows = ["09:00-12:00"]\n')
        is True
    )
    assert _pa('[phases]\nlist = ["a","b"]\n') is False  # wait + no per-phase schedule
    assert _pa("") is False  # no [phases]


def test_legacy_global_schedule_pause_omits_phase(tmp_path):
    """The legacy helper (used by non-phase-aware configs) emits schedule_paused
    with NO phase field — byte-identical to 0.2.7."""
    import types

    from agent_runner import config, schedule

    cfg = types.SimpleNamespace(
        schedule=config.ScheduleConfig(
            timezone="Asia/Shanghai", pause_windows=(schedule.parse_window("00:00-24:00"),)
        ),
        runtime=types.SimpleNamespace(stop_file=None),
    )
    stop = {"requested": True}  # break the pause loop immediately after emit
    serve_cmd._maybe_pause_for_schedule(
        cfg,
        tmp_path,
        stop,
        now_fn=lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=TZ),
        sleep_fn=lambda _s: None,
    )
    paused = next(e for e in _events(tmp_path) if e["event"] == "schedule_paused")
    assert "phase" not in paused


# --- --ignore-schedule bypass --------------------------------------------


def test_ignore_schedule_bypasses_phase_gate(monkeypatch, tmp_path):
    """--ignore-schedule skips select_phase entirely: no --phase, no pause even
    with an always-closed per-phase window."""
    argvs = _capture_run(monkeypatch)
    cfg_path = _cfg_path(
        tmp_path,
        '[schedule]\ntimezone = "Asia/Shanghai"\n'
        '[phases]\nlist = ["a"]\nphase_policy = "skip"\n'
        '[phases.a.schedule]\npause_windows = ["00:00-24:00"]\n',
    )
    args = Namespace(config=cfg_path, once=True, max_rounds=None, ignore_schedule=True)
    rc = serve_cmd.cmd(args)
    assert rc == 0
    assert argvs and _phase_of(argvs[0]) is None
    assert "schedule_paused" not in [e["event"] for e in _events(tmp_path / "logs")]
