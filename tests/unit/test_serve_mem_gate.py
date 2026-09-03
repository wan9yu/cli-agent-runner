"""Pre-round memory-pressure admission gate (Group 3 action half, part 1 of 2 --
see tests/integration/test_spawn_round_mem_floor.py for the mid-round hard floor).

The non-negotiable: a host under real memory pressure must stop starting new
rounds. _maybe_pause_for_memory_pressure folds into _select_and_gate (zero added
lines to serve_cmd.cmd's loop body -- see test_layer_2_loop_size.py) and mirrors
_maybe_pause_for_schedule's paired-event shape (round_deferred/round_resumed
instead of schedule_paused/schedule_resumed).

Every call below passes an explicit ``state={}`` (or ``mem_state={}``): the
production default is one dict shared for the whole serve process lifetime
(so the swap-rate delta tier survives across rounds without threading a new
line through cmd()'s loop body), and tests must not share that module-level
default with each other.
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

# The field-host shape: PSI unreadable (no /proc/pressure/memory, or psi=0),
# MemAvailable idling comfortably above the configured floor right up to the
# coma, and only the swap-out-rate delta -- gigabytes climbing sample over
# sample -- ever tells the true story. mem_avail_min_mb=40 (well below the 82
# these samples report) proves the combined-low tier genuinely cannot fire
# here; only the swap-rate delta can.
_FIELD_HOST_MEM_AVAIL_MIN_MB = 40


def _field_host_sample(swap_sout_gib: int) -> dict:
    return {
        "psi_some_avg10": None,
        "psi_full_avg10": None,
        "mem_free_mb": 30,
        "mem_available_mb": 82,
        "swap_sout": swap_sout_gib * 1024 * 1024 * 1024,
    }


def _events(log_dir):
    out = []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        out += [json.loads(x) for x in f.read_text().splitlines()]
    return out


def _real_cfg(tmp_path, mem_avail_min_mb: int | None = None):
    cfg = load_config(make_toml(tmp_path))
    if mem_avail_min_mb is not None:
        import dataclasses

        host_health_cfg = dataclasses.replace(
            cfg.monitor.host_health, mem_avail_min_mb=mem_avail_min_mb
        )
        monitor_cfg = dataclasses.replace(cfg.monitor, host_health=host_health_cfg)
        cfg = dataclasses.replace(cfg, monitor=monitor_cfg)
    return cfg


def test_no_pause_when_sample_reports_healthy(tmp_path):
    cfg = _real_cfg(tmp_path)
    stop = {"requested": False}
    paused = serve_cmd._maybe_pause_for_memory_pressure(
        cfg, tmp_path, stop, sample_fn=lambda: _HEALTHY_SAMPLE, state={}
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
        cfg, tmp_path, stop, sample_fn=fake_sample, clock=clock, chunk_s=5, state={}
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

    paused = serve_cmd._maybe_pause_for_memory_pressure(
        cfg, tmp_path, stop, sample_fn=fake_sample, state={}
    )
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

    out = serve_cmd._select_and_gate(
        cfg, args, tmp_path, stop, 1, sample_fn=fake_sample, mem_state={}
    )
    assert out is serve_cmd._PAUSED_CONTINUE
    assert [e["event"] for e in _events(tmp_path)] == ["round_deferred", "round_resumed"]


def test_memory_pressure_cfg_duck_type_is_monitor_host_health_config(tmp_path):
    """Sanity: the real Config's cfg.monitor.host_health is exactly the type
    host_health.memory_pressure expects (mem_avail_min_mb)."""
    cfg = _real_cfg(tmp_path)
    assert isinstance(cfg.monitor.host_health, MonitorHostHealthConfig)


def test_given_cache_poor_psi_off_host_when_pre_round_checked_twice_then_defers(tmp_path):
    """The real field-bug shape: PSI unreadable, MemAvailable idling at 82MB
    (comfortably above mem_avail_min_mb=40 -- combined-low genuinely cannot
    fire), only a gigabyte-scale swap-out-rate delta tells the truth. The
    FIRST pre-round check has no baseline (state starts empty) and correctly
    reports healthy -- there is nothing to diff against yet, an honest
    limitation, not a bug. The SECOND check (next round-start, swap_sout up
    2 GiB) has a real delta against the persisted state and defers.

    The stub keeps swap_sout climbing forever (a genuinely ballooning field
    host never stops paging), so once round 2 defers, its own re-check inside
    _pause_poll would see continued pressure indefinitely; the stub sets
    stop["requested"] on that first re-check so the test asserts the defer
    deterministically without racing a real sleep (the paired-event resume
    path itself is already covered by the PSI-based test above)."""
    cfg = _real_cfg(tmp_path, mem_avail_min_mb=_FIELD_HOST_MEM_AVAIL_MIN_MB)
    stop = {"requested": False}
    state: dict = {}
    calls = {"n": 0}

    def fake_sample():
        calls["n"] += 1
        if calls["n"] == 1:
            return _field_host_sample(swap_sout_gib=0)  # round 1: establishes the baseline
        if calls["n"] == 2:
            return _field_host_sample(swap_sout_gib=2)  # round 2: +2 GiB delta -> critical
        stop["requested"] = True  # the _pause_poll re-check: stop instead of racing a real sleep
        return _field_host_sample(swap_sout_gib=4)

    # Round 1 (first-ever pre-round check on this state): no baseline yet.
    paused_round1 = serve_cmd._maybe_pause_for_memory_pressure(
        cfg, tmp_path, stop, sample_fn=fake_sample, state=state, clock=FakeClock()
    )
    assert paused_round1 is False
    assert _events(tmp_path) == []

    # Round 2: a real delta against round 1's persisted sample. clock=FakeClock()
    # makes _pause_poll's one sleep_fn(chunk_s) call instant (virtual time only)
    # instead of racing a real 30s wait before the stub's stop["requested"] lands.
    paused_round2 = serve_cmd._maybe_pause_for_memory_pressure(
        cfg, tmp_path, stop, sample_fn=fake_sample, state=state, clock=FakeClock()
    )
    assert paused_round2 is True
    evs = _events(tmp_path)
    deferred = [e for e in evs if e["event"] == "round_deferred"]
    assert len(deferred) == 1
    assert deferred[0]["signal"] == "swap_out_rate"
    assert deferred[0]["severity"] == "critical"
    assert [e["event"] for e in evs] == ["round_deferred"]  # interrupted, not resumed
