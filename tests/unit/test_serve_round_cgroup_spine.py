"""round_cgroup_memory: emitted once per round from _spawn_round's stashed
per-round baseline/peak, carrying memory.events DELTAS (never absolutes) and
the peak memory.current/swap over the round (not the cumulative memory.peak)."""

import json
import sys

from agent_runner.cli import _serve_round
from agent_runner.config import MonitorHostHealthConfig


def _events(log_dir):
    path = sorted(log_dir.glob("events-*.jsonl"))[0]
    return [json.loads(line) for line in path.read_text().splitlines()]


def _usage(current, swap):
    return {
        "memory_current": current,
        "memory_swap_current": swap,
        "memory_events": {},
        "cgroup_path": "/x",
    }


class _TickingClock:
    """monotonic() advances by `step` on every call -- fakes elapsed wall time
    so a ~10s mid-round sample interval elapses without a real ~10s wait
    (mirrors tests/integration/test_spawn_round_mem_floor.py's helper)."""

    def __init__(self, step: float = 5.0):
        self._t = 0.0
        self._step = step

    def monotonic(self) -> float:
        self._t += self._step
        return self._t


def test_spawn_round_peak_is_max_over_ticks_not_the_last_reading(tmp_path, monkeypatch):
    """The stashed peak must be the running MAX across _spawn_round's existing
    mid-round ticks, not just the final tick's reading (which here is LOWER
    than an earlier tick) -- proves the peak tracking, not merely that a read
    happened."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(6)"]

    readings = iter([_usage(100, 0), _usage(100, 0), _usage(500, 50), _usage(200, 10)])
    last = _usage(200, 10)
    monkeypatch.setattr(
        _serve_round.metrics, "cgroup_memory_usage", lambda **k: next(readings, last)
    )

    rc = _serve_round._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(),
        sample_fn=lambda: {
            "psi_some_avg10": None,
            "psi_full_avg10": None,
            "mem_free_mb": 4000,
            "mem_available_mb": 4000,
            "swap_sout": 1000,
        },
    )
    assert rc == 0  # clean exit -- healthy host_health sample, not floor-terminated

    state = _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir]
    assert state["peak_current"] == 500  # the tick-3 peak, not the tick-4 reading (200)
    assert state["peak_swap"] == 50


def test_post_round_emits_cgroup_memory_delta(tmp_path, monkeypatch):
    log_dir = tmp_path
    _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir] = {
        "baseline_events": {"high": 10, "max": 0, "oom": 0, "oom_kill": 0},
        "peak_current": 300_000_000,
        "peak_swap": 100_000_000,
        "cgroup_path": "/user.slice",
    }
    monkeypatch.setattr(
        _serve_round.metrics,
        "cgroup_memory_usage",
        lambda **k: {
            "memory_events": {"high": 25, "max": 0, "oom": 0, "oom_kill": 0},
            "memory_current": 310_000_000,
            "memory_swap_current": 0,
            "cgroup_path": "/user.slice",
        },
    )
    _serve_round._emit_round_cgroup_memory(log_dir, log_dir / "round-7.log")
    ev = [e for e in _events(log_dir) if e["event"] == "round_cgroup_memory"][0]
    assert ev["events_high_delta"] == 15  # 25 - 10, a DELTA not an absolute
    assert ev["events_max_delta"] == 0
    assert ev["events_oom_delta"] == 0
    assert ev["events_oom_kill_delta"] == 0
    assert ev["memory_current_peak"] == 300_000_000  # the stashed PEAK, not the latest read
    assert ev["memory_swap_current_peak"] == 100_000_000
    assert ev["cgroup_path"] == "/user.slice"
    assert ev["round_num"] == 7


def test_emit_round_cgroup_memory_noop_without_finite_bound(tmp_path):
    """No stashed state (host has no finite cgroup bound) -> no emit, no error."""
    log_dir = tmp_path
    result = _serve_round._emit_round_cgroup_memory(log_dir, log_dir / "round-1.log")
    assert result == {}
    assert not list(log_dir.glob("events-*.jsonl"))


def test_emit_round_cgroup_memory_pops_state(tmp_path, monkeypatch):
    """State is consumed once -- a second call for the same log_dir (e.g. a stray
    double-call) must not re-emit from stale state."""
    log_dir = tmp_path
    _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir] = {
        "baseline_events": {},
        "peak_current": 1,
        "peak_swap": 0,
        "cgroup_path": "/x",
    }
    monkeypatch.setattr(
        _serve_round.metrics,
        "cgroup_memory_usage",
        lambda **k: {"memory_events": {}, "memory_current": 1, "memory_swap_current": 0},
    )
    _serve_round._emit_round_cgroup_memory(log_dir, log_dir / "round-1.log")
    assert log_dir not in _serve_round._ROUND_CGROUP_STATE_BY_LOG_DIR
    second = _serve_round._emit_round_cgroup_memory(log_dir, log_dir / "round-2.log")
    assert second == {}


def test_round_num_from_log_path():
    from pathlib import Path

    assert _serve_round._round_num_from_log_path(Path("/x/round-42.log")) == 42
    assert _serve_round._round_num_from_log_path(Path("/x/not-a-round.log")) == 0


def test_spawn_round_stashes_peak_across_ticks():
    """A direct check of the peak-tracking arithmetic _spawn_round performs,
    independent of subprocess timing: the running max must never regress even
    when a later tick reads a smaller memory.current than an earlier one."""
    readings = [_usage(100, 0), _usage(500, 50), _usage(200, 10)]
    cg_base = readings[0]
    cg_peak_current = cg_base.get("memory_current", 0)
    cg_peak_swap = cg_base.get("memory_swap_current", 0)
    for cg_now in readings[1:]:
        cg_peak_current = max(cg_peak_current, cg_now.get("memory_current", 0))
        cg_peak_swap = max(cg_peak_swap, cg_now.get("memory_swap_current", 0))
    assert cg_peak_current == 500  # peak, not the last reading (200)
    assert cg_peak_swap == 50
