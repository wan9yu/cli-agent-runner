"""Group 3 action half, part 2: the mid-round hard floor. A pre-round gate alone
cannot stop a single round that balloons mid-flight (metrics only sample at
round boundaries), so _spawn_round's existing 1s proc.wait loop also samples
host_health roughly every ~10s and _terminate_round's the round on critical
pressure.

The PSI-based test below exercises the mechanism, but is NOT the field-bug
coverage: the field host has no /proc/pressure/memory (PSI off), so it can
never produce a psi_full_avg10 sample. `test_given_cache_poor_psi_off_host_*`
is the real field-bug coverage. The mid-round floor's critical swap decision
is CUMULATIVE swap-out SINCE ROUND-START (the round-start sample is pinned as
the baseline for every subsequent tick), not a per-interval delta between two
~10s samples: on that host's SLOW SD/USB-backed swap (< ~3.2 MB/s once zram
fills) the per-10s delta never crosses the 32 MiB floor, so a per-interval
comparison stayed inert and one ballooning round could squeeze the host for
up to round_timeout_s. Cumulative-since-round-start crosses the floor over a
long round even at a slow trickle, gated on MemFree (~5MB, the "actively
dying" condition) rather than on the delta's exact rate.

Mirrors test_spawn_round_wedged.py's shape (real subprocess, TERM-first path)
but with an injected clock that fakes elapsed wall time so the test does not
block for a real ~10s interval."""

from __future__ import annotations

import sys

from agent_runner.cli import serve_cmd
from agent_runner.config import MonitorHostHealthConfig
from tests._test_helpers import read_events_for_current_month

_CRITICAL_SAMPLE = {
    "psi_some_avg10": 10.0,
    "psi_full_avg10": 70.0,  # >= the 60.0 default critical (systemd-oomd's proven bar)
    "mem_free_mb": 50,
    "mem_available_mb": 50,
    "swap_sout": 0,
}


# ~10 MB per ~10s tick -- well BELOW the 32 MiB per-interval noise floor, so an
# old per-interval comparison never fired; the field host's slow SD/USB swap
# can only trickle at roughly this rate once zram fills.
_SLOW_SWAP_STEP_BYTES = 10 * 1024 * 1024

_HEALTHY_SAMPLE = {
    "psi_some_avg10": None,
    "psi_full_avg10": None,
    "mem_free_mb": 4000,
    "mem_available_mb": 4000,
    "swap_sout": 1000,  # constant -- near-zero cumulative swap-out
}


def _slow_swap_sample_fn():
    """PSI unreadable, MemAvailable inflated at 82MB (comfortably above
    mem_avail_min_mb=40 -- combined-low genuinely cannot fire on MemAvailable
    alone), MemFree critically low (~5MB -- the "actively dying" condition
    critical is gated on), swap_sout climbing only ~10MB per ~10s tick -- a
    realistic SLOW SD/USB swap trickle whose PER-INTERVAL delta never crosses
    the 32 MiB floor. Only the CUMULATIVE swap-out since round-start crosses
    it, and only after enough ticks -- which is exactly the field episode this
    floor must terminate."""
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        return {
            "psi_some_avg10": None,
            "psi_full_avg10": None,
            "mem_free_mb": 5,
            "mem_available_mb": 82,
            "swap_sout": calls["n"] * _SLOW_SWAP_STEP_BYTES,
        }

    return _fn


class _TickingClock:
    """monotonic() advances by `step` on every call -- fakes elapsed wall time
    across _spawn_round's real-subprocess poll loop so a ~10s sample interval
    elapses without the test actually waiting ~10 real seconds."""

    def __init__(self, step: float = 5.0):
        self._t = 0.0
        self._step = step

    def monotonic(self) -> float:
        self._t += self._step
        return self._t


def test_critical_mid_round_pressure_terminates_round_and_emits(tmp_path):
    """PSI-based critical pressure exercises the mechanism (sampling cadence,
    terminate-and-emit, distinctness from round_supervisor_wedged) -- see
    test_given_cache_poor_psi_off_host_when_mid_round_pressure_checked_then_terminates
    below for the actual field-bug coverage (that host has no PSI at all)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,  # large enough that the mem floor trips first, not the ceiling
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(),
        sample_fn=lambda: _CRITICAL_SAMPLE,
    )
    assert rc != 0  # terminated, not a clean exit

    events = read_events_for_current_month(log_dir)
    terminated = [e for e in events if e.get("event") == "round_mem_terminated"]
    assert len(terminated) == 1
    assert terminated[0]["severity"] == "critical"
    assert terminated[0]["signal"] == "psi"

    # Distinct from the wall-clock-ceiling path: this is a memory-pressure kill,
    # not a wedged-round kill, so round_supervisor_wedged must NOT also fire.
    assert [e for e in events if e.get("event") == "round_supervisor_wedged"] == []


def test_given_cache_poor_psi_off_host_slow_swap_when_cumulative_crosses_then_terminates(tmp_path):
    """The real field-bug shape: PSI unreadable, MemAvailable inflated at 82MB
    well above mem_avail_min_mb=40 (combined-low genuinely cannot fire),
    MemFree critically low (~5MB), swap_out climbing only ~10MB per ~10s tick
    -- a SLOW SD/USB swap trickle whose per-interval delta stays below the
    32 MiB floor, so an old per-interval comparison would NEVER terminate.
    The floor now measures CUMULATIVE swap-out since round-start, so across
    enough ticks the accumulated delta crosses 32 MiB while MemFree stays
    critically low, and the round is terminated -- which is what makes this an
    actual coma-preventer on the field host regardless of swap-device speed."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        host_health_cfg=MonitorHostHealthConfig(mem_avail_min_mb=40),
        clock=_TickingClock(),
        sample_fn=_slow_swap_sample_fn(),
    )
    assert rc != 0  # terminated, not a clean exit

    events = read_events_for_current_month(log_dir)
    terminated = [e for e in events if e.get("event") == "round_mem_terminated"]
    assert len(terminated) == 1
    assert terminated[0]["severity"] == "critical"
    assert terminated[0]["signal"] == "swap_out_rate"
    assert [e for e in events if e.get("event") == "round_supervisor_wedged"] == []


def test_given_healthy_host_ample_memfree_when_mid_round_checked_then_no_terminate(tmp_path):
    """The negative control for the cumulative-swap floor: a healthy host with
    ample MemFree (~4000MB) and near-zero cumulative swap-out (constant
    swap_sout) is sampled across several ~10s ticks and never reaches
    critical, so the round runs to its own clean exit -- no round_mem_terminated
    and no round_supervisor_wedged."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(3)"]  # exits on its own

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        host_health_cfg=MonitorHostHealthConfig(mem_avail_min_mb=40),
        clock=_TickingClock(),
        sample_fn=lambda: _HEALTHY_SAMPLE,
    )
    assert rc == 0  # clean exit, never terminated by the floor

    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "round_mem_terminated"] == []
    assert [e for e in events if e.get("event") == "round_supervisor_wedged"] == []


def test_short_round_finishes_before_first_mem_check_interval(tmp_path):
    """A round that finishes fast never reaches even one ~10s sample interval --
    the mem floor is a floor sampled on a cadence, not a per-tick poller, so a
    quick healthy round completes untouched even though the stub would report
    critical if it were ever called."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "print('ok')"]
    calls = []

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(step=0.01),  # never crosses the 10s interval boundary
        sample_fn=lambda: calls.append(1) or _CRITICAL_SAMPLE,
    )
    assert rc == 0
    assert calls == []  # interval never elapsed -- sampler was never invoked
    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "round_mem_terminated"] == []


def test_mid_round_floor_disabled_by_default(tmp_path):
    """host_health_cfg defaults to None: existing callers (no mid-round floor
    wired) get byte-identical behavior -- the sampler is never even invoked."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "print('ok')"]

    calls = []
    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        sample_fn=lambda: calls.append(1) or _CRITICAL_SAMPLE,
    )
    assert rc == 0
    assert calls == []
