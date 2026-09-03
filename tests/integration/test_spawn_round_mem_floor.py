"""Group 3 action half, part 2: the mid-round hard floor. A pre-round gate alone
cannot stop a single round that balloons mid-flight (metrics only sample at
round boundaries), so _spawn_round's existing 1s proc.wait loop also samples
host_health roughly every ~10s and _terminate_round's the round on critical
pressure -- the actual coma-preventer for the field bug this closes.

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
    "psi_full_avg10": 2.0,
    "mem_free_mb": 50,
    "mem_available_mb": 50,
    "swap_sout": 0,
}


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
