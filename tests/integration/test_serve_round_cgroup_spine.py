"""round_cgroup_memory: emitted once per round from _spawn_round's stashed
per-round baseline/peak, carrying memory.events DELTAS (never absolutes) and
the peak memory.current/swap over the round (not the cumulative memory.peak)."""

import json
import sys

import pytest

from agent_runner import _procwait
from agent_runner.cli import _serve_cgroup, _serve_round
from agent_runner.config import MonitorHostHealthConfig
from tests._clock import PollLoopClock


@pytest.fixture(autouse=True)
def _fallback_wait_exit(monkeypatch):
    """_spawn_round's mid-round wait is one wait_exit call; its fast path is
    a real select/kqueue registration that blocks in real wall-clock and
    cannot be driven by this file's fake-monotonic PollLoopClock (see
    _procwait's module docstring) -- force the poll FALLBACK so the
    ~10s-interval ticks below stay paced by PollLoopClock's virtual time
    instead of a real select() timeout racing a fixed time.sleep(6) child."""
    monkeypatch.setattr(_procwait, "exit_fd", lambda proc: None)


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


def test_spawn_round_should_track_peak_as_max_across_ticks_when_later_reading_is_lower(
    tmp_path, monkeypatch
):
    """Proves the peak tracking, not merely that a read happened: the final
    tick's reading here is LOWER than an earlier tick, so a naive
    last-value-wins implementation would report the wrong peak."""
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
        clock=PollLoopClock(),
        sample_fn=lambda: {
            "psi_some_avg10": None,
            "psi_full_avg10": None,
            "mem_free_mb": 4000,
            "mem_available_mb": 4000,
            "swap_sout": 1000,
        },
    )

    assert rc == 0  # clean exit -- healthy host_health sample, not floor-terminated
    state = _serve_cgroup._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir]
    assert state["peak_current"] == 500  # the tick-3 peak, not the tick-4 reading (200)
    assert state["peak_swap"] == 50


def _prime_round_cgroup_emit(log_dir, monkeypatch, sample=None):
    _serve_cgroup._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir] = {
        "baseline_events": {"high": 10, "max": 0, "oom": 0, "oom_kill": 0},
        "peak_current": 300_000_000,
        "peak_swap": 100_000_000,
        "bounding_cgroup_path": "/user.slice",
    }
    monkeypatch.setattr(
        _serve_cgroup.metrics,
        "cgroup_memory_usage",
        lambda **k: {
            "memory_events": {"high": 25, "max": 0, "oom": 0, "oom_kill": 0},
            "memory_current": 310_000_000,
            "memory_swap_current": 0,
            "cgroup_path": "/user.slice",
        },
    )
    if sample is not None:
        monkeypatch.setattr(_serve_cgroup.metrics, "sample", lambda: sample)


def test_emit_round_cgroup_memory_should_emit_deltas_when_baseline_present(tmp_path, monkeypatch):
    log_dir = tmp_path
    _prime_round_cgroup_emit(log_dir, monkeypatch)

    _serve_cgroup._emit_round_cgroup_memory(log_dir, log_dir / "round-7.log", 7)

    ev = [e for e in _events(log_dir) if e["event"] == "round_cgroup_memory"][0]
    assert ev["events_high_delta"] == 15  # 25 - 10, a DELTA not an absolute
    assert ev["events_max_delta"] == 0
    assert ev["events_oom_delta"] == 0
    assert ev["events_oom_kill_delta"] == 0
    assert ev["memory_current_peak"] == 300_000_000  # the stashed PEAK, not the latest read
    assert ev["memory_swap_current_peak"] == 100_000_000
    assert ev["bounding_cgroup_path"] == "/user.slice"
    assert ev["round_num"] == 7
    assert "swap_headroom_bytes" not in ev  # dropped: no task owns real semantics for it yet
    assert "cgroup_path" not in ev  # that name means the LEAF in host_cgroup_memory_limit


def test_emit_round_cgroup_memory_should_carry_sample_psi_when_present(tmp_path, monkeypatch):
    """Corroborating IO/mem-PSI fields ride flat on round_cgroup_memory
    (not inside Pressure.context) when sample() actually read them."""
    log_dir = tmp_path
    _prime_round_cgroup_emit(
        log_dir,
        monkeypatch,
        sample={
            "io_psi_some_avg10": 1.25,
            "io_psi_full_avg10": 0.5,
            "psi_full_total": 42,
        },
    )

    _serve_cgroup._emit_round_cgroup_memory(log_dir, log_dir / "round-7.log", 7)

    ev = [e for e in _events(log_dir) if e["event"] == "round_cgroup_memory"][0]
    assert ev["io_psi_some_avg10"] == 1.25
    assert ev["io_psi_full_avg10"] == 0.5
    assert ev["psi_full_total"] == 42
    assert "context" not in ev  # never stuffed into Pressure.context


def test_emit_round_cgroup_memory_should_omit_sample_psi_when_unread(tmp_path, monkeypatch):
    log_dir = tmp_path
    _prime_round_cgroup_emit(
        log_dir,
        monkeypatch,
        sample={
            "io_psi_some_avg10": None,
            "io_psi_full_avg10": None,
            "psi_full_total": None,
        },
    )

    _serve_cgroup._emit_round_cgroup_memory(log_dir, log_dir / "round-7.log", 7)

    ev = [e for e in _events(log_dir) if e["event"] == "round_cgroup_memory"][0]
    assert "io_psi_some_avg10" not in ev
    assert "io_psi_full_avg10" not in ev
    assert "psi_full_total" not in ev
    assert ev["events_high_delta"] == 15


def test_emit_round_cgroup_memory_should_return_empty_when_no_finite_bound(tmp_path):
    log_dir = tmp_path

    result = _serve_cgroup._emit_round_cgroup_memory(log_dir, log_dir / "round-1.log", 1)

    assert result == ({}, None)
    assert not list(log_dir.glob("events-*.jsonl"))


def test_emit_round_cgroup_memory_should_skip_when_bounding_cgroup_vanished(tmp_path, monkeypatch):
    """The bounding cgroup existed at round start (state was stashed) but is no
    longer readable at round end (metrics reports {} -- vanished, or became
    unbounded). Emitting all-zero deltas against a stale bounding_cgroup_path
    would misreport "no pressure" when the truth is "can no longer tell" --
    skip the emit entirely instead."""
    log_dir = tmp_path
    _serve_cgroup._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir] = {
        "baseline_events": {"high": 10, "max": 0, "oom": 0, "oom_kill": 0},
        "peak_current": 300_000_000,
        "peak_swap": 100_000_000,
        "bounding_cgroup_path": "/user.slice",
    }
    monkeypatch.setattr(_serve_cgroup.metrics, "cgroup_memory_usage", lambda **k: {})

    result = _serve_cgroup._emit_round_cgroup_memory(log_dir, log_dir / "round-1.log", 1)

    assert result == ({}, None)
    assert not list(log_dir.glob("events-*.jsonl"))
    # Still consumed the stashed state -- no leak into a later round's call.
    assert log_dir not in _serve_cgroup._ROUND_CGROUP_STATE_BY_LOG_DIR


def test_emit_round_cgroup_memory_should_skip_when_baseline_events_empty(tmp_path, monkeypatch):
    """The spawn-start memory.events read failed (stashed as {}) but the
    round-end read succeeds: diffing against an empty baseline would report
    the cumulative-since-cgroup-creation counter (500 here) as if it were
    this round's delta. Skip the emit entirely instead of shipping a bogus
    absolute-as-delta payload."""
    log_dir = tmp_path
    _serve_cgroup._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir] = {
        "baseline_events": {},
        "peak_current": 300_000_000,
        "peak_swap": 100_000_000,
        "bounding_cgroup_path": "/user.slice",
    }
    monkeypatch.setattr(
        _serve_cgroup.metrics,
        "cgroup_memory_usage",
        lambda **k: {
            "memory_events": {"high": 500, "max": 0, "oom": 0, "oom_kill": 0},
            "memory_current": 310_000_000,
            "memory_swap_current": 0,
            "cgroup_path": "/user.slice",
        },
    )

    result = _serve_cgroup._emit_round_cgroup_memory(log_dir, log_dir / "round-1.log", 1)

    assert result == ({}, None)
    assert not list(log_dir.glob("events-*.jsonl"))
    # Still consumed the stashed state -- no leak into a later round's call.
    assert log_dir not in _serve_cgroup._ROUND_CGROUP_STATE_BY_LOG_DIR


def test_emit_round_cgroup_memory_should_consume_state_once_when_called_twice(
    tmp_path, monkeypatch
):
    """State is consumed once -- a second call for the same log_dir (e.g. a stray
    double-call) must not re-emit from stale state."""
    log_dir = tmp_path
    _serve_cgroup._ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir] = {
        "baseline_events": {},
        "peak_current": 1,
        "peak_swap": 0,
        "bounding_cgroup_path": "/x",
    }
    monkeypatch.setattr(
        _serve_cgroup.metrics,
        "cgroup_memory_usage",
        lambda **k: {"memory_events": {}, "memory_current": 1, "memory_swap_current": 0},
    )

    _serve_cgroup._emit_round_cgroup_memory(log_dir, log_dir / "round-1.log", 1)

    assert log_dir not in _serve_cgroup._ROUND_CGROUP_STATE_BY_LOG_DIR

    second = _serve_cgroup._emit_round_cgroup_memory(log_dir, log_dir / "round-2.log", 2)

    assert second == ({}, None)


def _growth_pressure(rate=999.0, threshold=512.0):
    from agent_runner import host_health

    return host_health.Pressure(
        severity="warning",
        signal="cgroup_growth_rate",
        message=f"memory growing {rate:.0f} MB/min (>= {threshold:.0f})",
        context={"rate_mb_per_min": rate, "threshold_mb_per_min": threshold},
    )


def test_spawn_round_should_emit_growth_warning_once_per_crossing_episode_when_cgroup_source_used(
    tmp_path, monkeypatch
):
    """host_health.cgroup_growth_rate_pressure is patched to fire on ticks 2-3
    (one sustained episode) then clear on tick 4 -- exactly one emit, not one
    per critical tick."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(6)"]

    usages = iter([_usage(0, 0), _usage(1, 0), _usage(2, 0), _usage(3, 0), _usage(4, 0)])
    monkeypatch.setattr(
        _serve_round.metrics, "cgroup_memory_usage", lambda **k: next(usages, _usage(4, 0))
    )
    verdicts = iter([None, _growth_pressure(), _growth_pressure(), None])
    monkeypatch.setattr(
        _serve_round.host_health,
        "cgroup_growth_rate_pressure",
        lambda rate, cfg: next(verdicts, None),
    )

    _serve_round._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=PollLoopClock(),
        sample_fn=lambda: {
            "psi_some_avg10": None,
            "psi_full_avg10": None,
            "mem_free_mb": 4000,
            "mem_available_mb": 4000,
            "swap_sout": 0,
        },
    )

    warnings = [e for e in _events(log_dir) if e["event"] == "cgroup_growth_rate_warning"]
    assert len(warnings) == 1
    assert warnings[0]["source"] == "cgroup"
    assert warnings[0]["round_num"] == 1


def test_spawn_round_should_use_rss_sum_source_when_no_finite_cgroup_bound(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(6)"]

    monkeypatch.setattr(_serve_round.metrics, "cgroup_memory_usage", lambda **k: {})
    rss_readings = iter([100_000_000, 800_000_000, 1_500_000_000])
    monkeypatch.setattr(
        _serve_round, "children_rss_sum_bytes", lambda proc: next(rss_readings, 1_500_000_000)
    )

    _serve_round._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=PollLoopClock(),
        sample_fn=lambda: {
            "psi_some_avg10": None,
            "psi_full_avg10": None,
            "mem_free_mb": 4000,
            "mem_available_mb": 4000,
            "swap_sout": 0,
        },
    )

    warnings = [e for e in _events(log_dir) if e["event"] == "cgroup_growth_rate_warning"]
    assert warnings
    assert all(w["source"] == "rss_sum" for w in warnings)


def test_spawn_round_should_never_pass_a_negative_rate_when_memory_drops_between_ticks(
    tmp_path, monkeypatch
):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(6)"]

    usages = iter([_usage(0, 0), _usage(500_000_000, 0), _usage(100_000_000, 0)])
    monkeypatch.setattr(
        _serve_round.metrics,
        "cgroup_memory_usage",
        lambda **k: next(usages, _usage(100_000_000, 0)),
    )
    rates_seen: list = []

    def _spy(rate, cfg):
        rates_seen.append(rate)
        return None

    monkeypatch.setattr(_serve_round.host_health, "cgroup_growth_rate_pressure", _spy)

    _serve_round._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=PollLoopClock(),
        sample_fn=lambda: {
            "psi_some_avg10": None,
            "psi_full_avg10": None,
            "mem_free_mb": 4000,
            "mem_available_mb": 4000,
            "swap_sout": 0,
        },
    )

    real_rates = [r for r in rates_seen if r is not None]
    assert real_rates, "growth-rate derivative never ran"
    assert all(r >= 0 for r in real_rates)


def test_spawn_round_should_not_crash_when_cgroup_source_fails_open_mid_round(
    tmp_path, monkeypatch
):
    """A `{}` cgroup read mid-round (bounding cgroup vanished) must be treated
    as 'cannot compute this tick', never as a real 0-byte reading, and must
    never raise out of the tick loop."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(6)"]

    usages = iter([_usage(0, 0), _usage(500_000_000, 0), {}, _usage(600_000_000, 0)])
    monkeypatch.setattr(
        _serve_round.metrics,
        "cgroup_memory_usage",
        lambda **k: next(usages, _usage(600_000_000, 0)),
    )

    rc = _serve_round._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=PollLoopClock(),
        sample_fn=lambda: {
            "psi_some_avg10": None,
            "psi_full_avg10": None,
            "mem_free_mb": 4000,
            "mem_available_mb": 4000,
            "swap_sout": 0,
        },
    )

    assert rc == 0  # clean exit -- the {} tick did not crash the round
