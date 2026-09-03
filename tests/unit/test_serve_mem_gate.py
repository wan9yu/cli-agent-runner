"""Pre-round memory-pressure admission gate (Group 3 action half, part 1 of 2 --
see tests/integration/test_spawn_round_mem_floor.py for the mid-round hard floor).

The non-negotiable: a host under real memory pressure must stop starting new
rounds. _maybe_pause_for_memory_pressure folds into _select_and_gate (zero added
lines to serve_cmd.cmd's loop body -- see test_layer_2_loop_size.py) and mirrors
_maybe_pause_for_schedule's paired-event shape (round_deferred/round_resumed
instead of schedule_paused/schedule_resumed).
"""

from __future__ import annotations

import json
from argparse import Namespace

from agent_runner.cli import serve_cmd
from agent_runner.config import MonitorHostHealthConfig, load_config
from tests._clock import FakeClock
from tests._test_helpers import make_toml

_CRITICAL_SAMPLE = {
    "psi_some_avg10": 10.0,
    "psi_full_avg10": 2.0,
    "mem_free_mb": 50,
    "mem_available_mb": 50,
    "swap_sout": 0,
}
_HEALTHY_SAMPLE = {
    "psi_some_avg10": 0.0,
    "psi_full_avg10": 0.0,
    "mem_free_mb": 5000,
    "mem_available_mb": 5000,
    "swap_sout": 0,
}


def _events(log_dir):
    out = []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        out += [json.loads(x) for x in f.read_text().splitlines()]
    return out


def _real_cfg(tmp_path):
    return load_config(make_toml(tmp_path))


def test_no_pause_when_sample_reports_healthy(tmp_path):
    cfg = _real_cfg(tmp_path)
    stop = {"requested": False}
    paused = serve_cmd._maybe_pause_for_memory_pressure(
        cfg, tmp_path, stop, sample_fn=lambda: _HEALTHY_SAMPLE
    )
    assert paused is False
    assert _events(tmp_path) == []


def test_pressure_defers_then_resumes_and_emits_paired_events(tmp_path):
    """A stub host_health reporting critical pressure defers the round
    (round_deferred), then resumes (round_resumed) once a later sample clears --
    the paired-event shape that keeps detect_supervisor_stale quiet."""
    cfg = _real_cfg(tmp_path)
    stop = {"requested": False}
    calls = {"n": 0}

    def fake_sample():
        calls["n"] += 1
        return _CRITICAL_SAMPLE if calls["n"] == 1 else _HEALTHY_SAMPLE

    clock = FakeClock()
    paused = serve_cmd._maybe_pause_for_memory_pressure(
        cfg, tmp_path, stop, sample_fn=fake_sample, clock=clock, chunk_s=5
    )
    assert paused is True
    evs = _events(tmp_path)
    assert [e["event"] for e in evs] == ["round_deferred", "round_resumed"]
    deferred = evs[0]
    assert deferred["signal"] == "psi"
    assert deferred["severity"] == "critical"
    assert "resumed" not in deferred
    resumed = evs[1]
    assert resumed["deferred_for_s"] >= 0


def test_pressure_defer_interrupted_by_stop(tmp_path):
    """A SIGTERM (stop["requested"]) during a defer breaks the poll without a
    resume -- an interrupted defer is a termination, not a clearance, mirroring
    _maybe_pause_for_schedule's stop semantics."""
    cfg = _real_cfg(tmp_path)
    stop = {"requested": False}

    def fake_sample():
        stop["requested"] = True
        return _CRITICAL_SAMPLE

    paused = serve_cmd._maybe_pause_for_memory_pressure(cfg, tmp_path, stop, sample_fn=fake_sample)
    assert paused is True
    assert [e["event"] for e in _events(tmp_path)] == ["round_deferred"]


def test_select_and_gate_defers_on_pressure_before_ignore_schedule_check(tmp_path):
    """Wiring: _select_and_gate itself consults the memory gate first -- even
    --ignore-schedule (a scheduling-only override) must not bypass a safety
    gate on a different axis."""
    cfg = _real_cfg(tmp_path)
    stop = {"requested": False}
    args = Namespace(ignore_schedule=True)
    calls = {"n": 0}

    def fake_sample():
        calls["n"] += 1
        return _CRITICAL_SAMPLE if calls["n"] == 1 else _HEALTHY_SAMPLE

    out = serve_cmd._select_and_gate(cfg, args, tmp_path, stop, 1, sample_fn=fake_sample)
    assert out is serve_cmd._PAUSED_CONTINUE
    assert [e["event"] for e in _events(tmp_path)] == ["round_deferred", "round_resumed"]


def test_memory_pressure_cfg_duck_type_is_monitor_host_health_config(tmp_path):
    """Sanity: the real Config's cfg.monitor.host_health is exactly the type
    host_health.memory_pressure expects (mem_avail_min_mb)."""
    cfg = _real_cfg(tmp_path)
    assert isinstance(cfg.monitor.host_health, MonitorHostHealthConfig)
