"""_spawn_round's mid-round wait: one wait_exit call replaces the old
proc.wait(timeout=_ROUND_POLL_TICK_S) busy-poll. This is a MECHANISM change
only -- the decision surface (terminate verdict, critical-streak cap) must
stay byte-identical. tests/integration/test_spawn_round_mem_floor.py carries
the full property suite (scripted wait_exit + FakeClock). This file keeps
the real-child returncode plumbing test, plus a decision-surface guard on
the same scripted seam.
"""

from __future__ import annotations

import sys

from agent_runner.cli import _serve_round
from agent_runner.config import MonitorHostHealthConfig
from tests._clock import FakeClock, install_scripted_round
from tests._test_helpers import read_events_for_current_month

_CRITICAL_SAMPLE = {
    "psi_some_avg10": 10.0,
    "psi_full_avg10": 70.0,  # >= the 60.0 default critical (systemd-oomd's proven bar)
    "mem_free_mb": 50,
    "mem_available_mb": 50,
    "swap_sout": 0,
}


def test_spawn_round_should_return_returncode_via_wait_exit_when_proc_exits(tmp_path):
    """No host_health_cfg -- the mid-round floor is off, so this exercises
    only wait_exit's "exited" outcome (whichever path the host supports:
    real pidfd/kqueue locally, poll fallback elsewhere) carrying the child's
    own returncode back out, exactly as the old proc.wait loop did."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import sys; sys.exit(3)"]

    rc = _serve_round._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
    )

    assert rc == 3


def test_spawn_round_decision_surface_should_match_when_mem_critical(tmp_path, monkeypatch):
    """Sustained-critical terminate at streak 3 via scripted wait_exit +
    FakeClock -- same decision surface as the mem_floor PSI test, no live child."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    clock = FakeClock()
    install_scripted_round(monkeypatch, clock)

    rc = _serve_round._spawn_round(
        ["true"],
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=clock,
        sample_fn=lambda: _CRITICAL_SAMPLE,
    )

    assert rc != 0  # terminated, not a clean exit

    events = read_events_for_current_month(log_dir)
    terminated = [e for e in events if e.get("event") == "round_mem_terminated"]
    assert len(terminated) == 1
    assert terminated[0]["consecutive"] == 3  # default critical_consecutive_samples

    samples = [e for e in events if e.get("event") == "round_mem_critical_sample"]
    assert [s["consecutive"] for s in samples] == [1, 2, 3]
