"""serve loop per-phase scheduling: --phase passthrough, wait/skip policy, and
the byte-identical legacy path (0.2.9).

Drives serve_cmd.cmd() with subprocess.run mocked to capture the round argv and
an injected clock, mirroring test_serve_schedule_gate.py.
"""

from __future__ import annotations

import json
import subprocess
import time
from argparse import Namespace
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_runner.cli import serve_cmd
from tests._clock import FakeClock
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
    monkeypatch.setattr(serve_cmd.SYSTEM_CLOCK, "sleep", lambda _s: setattr(clock, "opened", True))
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


# --- 0.2.10 throttle-aware skip ------------------------------------------


def _seed_throttle(log_dir, *, phase, reset_at=None, classification="rate_limit_model", agent="x"):
    """Write a transient_error_detected event so _check_throttle_state sees an
    active throttle on ``phase`` at serve loop top."""
    from datetime import UTC, datetime

    log_dir.mkdir(parents=True, exist_ok=True)
    reset_at = reset_at if reset_at is not None else int(time.time() + 3600)
    path = log_dir / f"events-{datetime.now(UTC).strftime('%Y-%m')}.jsonl"
    with path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "event": "transient_error_detected",
                    "classification": classification,
                    "agent": agent,
                    "reset_at_epoch": reset_at,
                    "round_num": 1,
                    "phase": phase,
                    "raw": "x",
                }
            )
            + "\n"
        )


_TWO_AGENTS = (
    '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n'
    '[phases.a.agent]\ncommand = ["claude"]\n'
    '[phases.b.agent]\ncommand = ["gemini"]\n'
)


def test_throttled_agent_skips_to_healthy_sibling(monkeypatch, tmp_path):
    argvs = _capture_run(monkeypatch)
    _seed_throttle(tmp_path / "logs", phase="a", agent="claude")  # claude throttled, gemini healthy
    cfg_path = _cfg_path(tmp_path, _TWO_AGENTS)
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    assert _phase_of(argvs[0]) == "b"  # rotated past claude-throttled a
    skip = [e for e in _events(tmp_path / "logs") if e["event"] == "schedule_phase_skipped"]
    assert skip and skip[0]["skipped"] == ["a"] and skip[0]["chosen"] == "b"


def test_skip_around_does_not_apply_global_back_off(monkeypatch, tmp_path):
    """A throttled agent under skip rotates to a healthy-agent sibling WITHOUT the sleep."""
    argvs = _capture_run(monkeypatch)
    called = []
    monkeypatch.setattr(serve_cmd, "_apply_back_off", lambda *a, **k: called.append(True))
    _seed_throttle(tmp_path / "logs", phase="a", agent="claude")
    cfg_path = _cfg_path(tmp_path, _TWO_AGENTS)
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    assert _phase_of(argvs[0]) == "b"
    assert called == []  # rotation handled it; no back-off


def test_shared_agent_throttle_skips_all_its_phases(tmp_path):
    """Two phases sharing ONE throttled agent are BOTH skipped — no hammering the
    rate-limited provider (the phase→agent re-key fix)."""
    from agent_runner.config import load_config

    reset_at = int(time.time() + 3600)
    _seed_throttle(tmp_path / "logs", phase="a", agent="claude", reset_at=reset_at)
    cfg = load_config(
        _cfg_path(
            tmp_path,
            '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n'
            '[phases.a.agent]\ncommand = ["claude"]\n'
            '[phases.b.agent]\ncommand = ["claude"]\n',
        )
    )
    throttled, wake = serve_cmd._throttle_skip_context(cfg, tmp_path / "logs")
    assert throttled == frozenset({"a", "b"})
    assert wake == reset_at


def test_two_agents_throttled_third_agent_runs(monkeypatch, tmp_path):
    """Two agents throttled → both their phases skipped; a third healthy agent runs."""
    argvs = _capture_run(monkeypatch)
    _seed_throttle(tmp_path / "logs", phase="a", agent="claude")
    _seed_throttle(tmp_path / "logs", phase="b", agent="gemini")
    cfg_path = _cfg_path(
        tmp_path,
        '[phases]\nlist = ["a","b","c"]\nphase_policy = "skip"\n'
        '[phases.a.agent]\ncommand = ["claude"]\n'
        '[phases.b.agent]\ncommand = ["gemini"]\n'
        '[phases.c.agent]\ncommand = ["codewhale"]\n',
    )
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    assert _phase_of(argvs[0]) == "c"
    skip = [e for e in _events(tmp_path / "logs") if e["event"] == "schedule_phase_skipped"]
    assert skip and set(skip[0]["skipped"]) == {"a", "b"} and skip[0]["chosen"] == "c"


def test_ignore_schedule_throttle_still_backs_off(monkeypatch, tmp_path):
    """--ignore-schedule keeps the 0.2.9 global back-off (defer must not fire)."""
    argvs = _capture_run(monkeypatch)
    called = []
    monkeypatch.setattr(serve_cmd, "_apply_back_off", lambda *a, **k: called.append(True))
    _seed_throttle(tmp_path / "logs", phase="a")
    cfg_path = _cfg_path(tmp_path, '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n')
    args = Namespace(config=cfg_path, once=True, max_rounds=None, ignore_schedule=True)
    rc = serve_cmd.cmd(args)
    assert rc == 0
    assert called == [True]  # global back-off, not rotation
    assert _phase_of(argvs[0]) is None  # ignore-schedule appends no --phase


def test_all_throttled_routes_to_pause_with_wake_epoch(monkeypatch, tmp_path):
    """When every candidate phase is throttled, _select_and_gate pauses (does not
    launch), excluding the throttled phases from the window poll and waking at reset_at."""
    from agent_runner.config import load_config

    reset_at = int(time.time() + 3600)
    cfg = load_config(_cfg_path(tmp_path, '[phases]\nlist = ["a"]\nphase_policy = "skip"\n'))
    spy = {}
    monkeypatch.setattr(
        serve_cmd,
        "_pause_until_selectable",
        lambda *a, **k: spy.update(k) or spy.update({"called": True}),
    )
    stop = {"requested": False}
    out = serve_cmd._select_and_gate(
        cfg,
        _args(tmp_path),
        tmp_path / "logs",
        stop,
        1,
        throttled_phases=frozenset({"a"}),
        wake_epoch=reset_at,
    )
    assert out is serve_cmd._PAUSED_CONTINUE
    assert spy.get("called") and spy["throttled_phases"] == frozenset({"a"})
    assert spy["wake_epoch"] == reset_at


def test_skip_around_clear_emits_one_recovered(monkeypatch, tmp_path):
    """A throttle that cleared via skip-around (reset_at already past, no
    breadcrumb) emits exactly one transient_error_recovered at loop top, with
    the detected event's classification/agent."""
    _capture_run(monkeypatch)
    # reset_at in the PAST → _check_throttle_state None → pending_recovered fires.
    _seed_throttle(
        tmp_path / "logs",
        phase="a",
        reset_at=int(time.time() - 60),
        classification="rate_limit_model",
        agent="deepseek-cli",
    )
    cfg_path = _cfg_path(tmp_path, '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n')
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    rec = [e for e in _events(tmp_path / "logs") if e["event"] == "transient_error_recovered"]
    assert len(rec) == 1
    assert rec[0]["classification"] == "rate_limit_model"
    assert rec[0]["agent"] == "deepseek-cli"
    assert rec[0]["throttled_for_s"] >= 0


def test_legacy_skip_action_emits_no_recovered_breadcrumb(monkeypatch, tmp_path):
    """Byte-identical guard: a non-[phases] config with transient_error_action=skip
    and a cleared throttle must NOT emit the 0.2.10 breadcrumb (0.2.9 emitted none)."""
    _capture_run(monkeypatch)
    _seed_throttle(tmp_path / "logs", phase="", reset_at=int(time.time() - 60))
    cfg_path = make_toml_with_sections(tmp_path, runtime_extra='transient_error_action = "skip"\n')
    rc = serve_cmd.cmd(
        Namespace(config=cfg_path, once=True, max_rounds=None, ignore_schedule=False)
    )
    assert rc == 0
    rec = [e for e in _events(tmp_path / "logs") if e["event"] == "transient_error_recovered"]
    assert rec == []  # legacy path stays silent


def test_skip_ignores_throttle_for_unused_agent(monkeypatch, tmp_path):
    """Under skip, a throttle whose agent maps to no configured phase is ignored: the
    round runs normally — skip never applies the global back-off, and no phase is
    skipped. The join is by agent, so the detected event's phase field is irrelevant."""
    argvs = _capture_run(monkeypatch)
    called = []
    monkeypatch.setattr(serve_cmd, "_apply_back_off", lambda *a, **k: called.append(True))
    _seed_throttle(
        tmp_path / "logs", phase="", agent="unused-agent", reset_at=int(time.time() + 3600)
    )
    cfg_path = _cfg_path(tmp_path, '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n')
    rc = serve_cmd.cmd(_args(cfg_path))
    assert rc == 0
    assert _phase_of(argvs[0]) == "a"  # nothing throttled for our agents → normal rotation
    assert called == []  # skip never applies the global back-off
    assert not [e for e in _events(tmp_path / "logs") if e["event"] == "schedule_phase_skipped"]


def _paused_sel():
    from agent_runner.phase_select import Selection

    return Selection(
        phase=None,
        paused=True,
        resume_at=None,
        resume_phase=None,
        resume_timezone=None,
        skipped=[],
        active_window=None,
    )


def test_pause_excludes_throttled_from_window_poll(tmp_path):
    """Busy-spin guard: a throttled phase whose window is OPEN must be excluded
    from the poll, so the loop actually sleeps instead of instant-resuming."""
    from agent_runner.config import load_config

    cfg = load_config(_cfg_path(tmp_path, '[phases]\nlist = ["a"]\nphase_policy = "skip"\n'))
    clock = FakeClock(epoch=1000.0)  # one seam for epoch + sleep
    stop = {"requested": False}
    serve_cmd._pause_until_selectable(
        cfg,
        tmp_path / "logs",
        stop,
        1,
        _paused_sel(),
        throttled_phases=frozenset({"a"}),  # a's window is always open, but throttled
        wake_epoch=1002,  # 2s ahead; sleeps advance the clock to it
        now_fn=lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=TZ),
        clock=clock,
        chunk_s=1,
    )
    assert clock.slept  # it slept — did NOT instant-resume on the throttled-but-open phase


def test_pause_wakes_at_wake_epoch(tmp_path):
    """The throttle's reset_at is an extra wake trigger even with no open window."""
    from agent_runner.config import load_config

    cfg = load_config(_cfg_path(tmp_path, '[phases]\nlist = ["a"]\nphase_policy = "skip"\n'))
    clock = FakeClock(epoch=1000.0)
    stop = {"requested": False}
    serve_cmd._pause_until_selectable(
        cfg,
        tmp_path / "logs",
        stop,
        1,
        _paused_sel(),
        throttled_phases=frozenset({"a"}),
        wake_epoch=1000,  # clock is at 1000 → reset already reached, resume at once
        now_fn=lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=TZ),
        clock=clock,
        chunk_s=1,
    )
    assert not clock.slept  # woke on reset_at without sleeping
    assert any(e["event"] == "schedule_resumed" for e in _events(tmp_path / "logs"))


def test_pause_loop_driven_by_fakeclock_sleep_advancing_time(tmp_path):
    """Drive the real pause loop with the shared FakeClock: its sleep() advances
    virtual epoch AND monotonic, so the loop wakes deterministically once the
    injected clock crosses wake_epoch (no monkeypatch, no real sleep). One clock
    now drives epoch, sleep AND the monotonic-based paused_for_s."""
    from agent_runner.config import load_config

    cfg = load_config(_cfg_path(tmp_path, '[phases]\nlist = ["a"]\nphase_policy = "skip"\n'))
    clock = FakeClock(epoch=1000.0)
    stop = {"requested": False}
    serve_cmd._pause_until_selectable(
        cfg,
        tmp_path / "logs",
        stop,
        1,
        _paused_sel(),
        throttled_phases=frozenset({"a"}),
        wake_epoch=1025,  # 25s ahead; chunked sleeps advance the FakeClock to it
        now_fn=lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=TZ),
        clock=clock,
        chunk_s=10,
    )
    assert clock.slept == [10, 10, 10]  # 1000→1030 crosses 1025
    assert clock.epoch() == 1030.0 and clock.monotonic() == 30.0  # sleep advanced both
    assert any(e["event"] == "schedule_resumed" for e in _events(tmp_path / "logs"))


def test_pause_wakes_on_sibling_window_before_reset(tmp_path):
    """min(window, reset_at): a non-throttled sibling whose window is open resumes
    the loop before the far-future throttle reset."""
    from agent_runner.config import load_config

    cfg = load_config(_cfg_path(tmp_path, '[phases]\nlist = ["a","b"]\nphase_policy = "skip"\n'))
    clock = FakeClock(epoch=1000.0)  # far below wake_epoch, so only b's window can resume
    stop = {"requested": False}
    serve_cmd._pause_until_selectable(
        cfg,
        tmp_path / "logs",
        stop,
        1,
        _paused_sel(),
        throttled_phases=frozenset({"a"}),  # b is not throttled and its window is open
        wake_epoch=9000,  # reset far away; clock stays below it so window wins
        now_fn=lambda _tz: datetime(2026, 8, 22, 10, 0, tzinfo=TZ),
        clock=clock,
        chunk_s=1,
    )
    assert not clock.slept  # b's open window resumed immediately, before reset_at
    assert any(e["event"] == "schedule_resumed" for e in _events(tmp_path / "logs"))


def test_second_serve_refused_when_lock_held(tmp_path, capsys):
    """Loop-lifetime single-instance guard: a second serve refuses to start (exit 1)
    while another holds the serve-scoped lock (0.2.11)."""
    import fcntl
    import os

    cfg_path = _cfg_path(tmp_path, '[phases]\nlist = ["a"]\nphase_policy = "skip"\n')
    log_dir = tmp_path / "logs"
    fd = os.open(log_dir / "serve.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # stand in for the running serve
    try:
        rc = serve_cmd.cmd(_args(cfg_path))
        assert rc == 1
        assert "already running" in capsys.readouterr().err
        assert not (log_dir / "serve.pid").exists()  # refused before writing its pid
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_serve_releases_lock_on_exit(tmp_path, monkeypatch):
    """After a serve run exits, the lock is free for the next serve (once=True)."""
    import fcntl
    import os

    _capture_run(monkeypatch)
    cfg_path = _cfg_path(tmp_path, '[phases]\nlist = ["a"]\nphase_policy = "skip"\n')
    assert serve_cmd.cmd(_args(cfg_path)) == 0
    # lock is released → we can acquire it non-blocking
    fd = os.open(tmp_path / "logs" / "serve.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # would raise if still held
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
