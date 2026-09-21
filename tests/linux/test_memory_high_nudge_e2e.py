"""v0.3.7 nudge E2E (correctness gate of record): a real round-leader child
that traps SIGTERM proves the early-SIGTERM nudge reaches the agent within one
mid-round tick of the FIRST critical sample, the round ends tier="nudge", and
the hard _terminate_round path never escalates.

Also covers the double-emit safety-critical case (opus review of Task 5): a
nudge that does NOT end the round (the agent ignores SIGTERM) must not mask
the hard floor -- critical_streak keeps climbing past the nudge and the hard
_terminate_round path still fires, emitting a SECOND round_mem_terminated
(tier="terminate"). round_was_mem_terminated(...) must still read True
exactly once for that round -- the per-round boolean is not double-counted
by two events."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_runner import _procwait
from agent_runner._throttle import round_was_mem_terminated
from agent_runner.api import emit_round_substrate_before
from agent_runner.cli import serve_cmd
from agent_runner.config import (
    MonitorHostHealthConfig,
    _HostHealthPressureConfig,
)
from tests._clock import PollLoopClock
from tests._test_helpers import read_events_for_current_month
from tests.integration.test_spawn_round_mem_floor import (
    _CRITICAL_SAMPLE,
    _HEALTHY_SAMPLE,
)


@pytest.fixture(autouse=True)
def _fallback_wait_exit(monkeypatch):
    """Same rationale as test_spawn_round_mem_floor.py's own autouse fixture
    (see its docstring): PollLoopClock's monotonic() is fake, but the fast
    exit_fd path's select.select still blocks in REAL wall-clock for however
    many (fake) seconds `remaining` computes to -- so force the poll
    FALLBACK and shrink its real per-tick cadence to 0.01s. This does not
    change what's under test: proc.terminate() below still sends a REAL
    SIGTERM to a REAL subprocess; only the supervisor's own exit-observation
    mechanism (poll vs pidfd/kqueue) is swapped for test speed."""
    monkeypatch.setattr(_procwait, "exit_fd", lambda proc: None)
    monkeypatch.setattr(_procwait, "_POLL_TICK_S", 0.01)


def _sigterm_trapping_child_argv(marker: Path, ready: Path) -> list[str]:
    """A leader child that installs a SIGTERM trap, signals ``ready`` once
    installed, then writes ``marker`` and exits 0 when it actually receives
    SIGTERM -- so the test proves the SIGTERM reached (and was handled by)
    the child, not a bare default-disposition kill."""
    return [
        sys.executable,
        "-c",
        "import signal, sys, pathlib, time\n"
        f"m = pathlib.Path({str(marker)!r})\n"
        f"r = pathlib.Path({str(ready)!r})\n"
        "signal.signal(signal.SIGTERM, lambda *_: (m.write_text('term'), sys.exit(0)))\n"
        "r.touch()\n"
        "while True:\n"
        "    time.sleep(0.01)\n",
    ]


def _sigterm_ignoring_child_argv(ready: Path) -> list[str]:
    """A leader child that installs SIG_IGN, signals ``ready`` once
    installed, and then never exits on its own -- forces the nudge's bare
    ``proc.terminate()`` to be a no-op, so critical_streak keeps climbing
    past the nudge and the hard ``_terminate_round`` path fires too, reaping
    the child via killpg."""
    return [
        sys.executable,
        "-c",
        "import signal, pathlib, time\n"
        f"r = pathlib.Path({str(ready)!r})\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "r.touch()\n"
        "while True:\n"
        "    time.sleep(0.01)\n",
    ]


def _sample_fn_once_ready(ready: Path, critical_sample: dict):
    """Returns a HEALTHY sample (critical_streak stays at 0) until ``ready``
    exists -- i.e. until the child has actually installed its SIGTERM
    disposition -- then returns ``critical_sample`` from then on.

    Without this gate, the nudge's ``proc.terminate()`` can race the child's
    own ``signal.signal()`` install: Python interpreter startup easily
    outlasts the first mid-round tick under the poll-fallback fixture (a
    couple of real milliseconds), so a SIGTERM delivered before the handler
    is installed runs under the OS default disposition -- an immediate kill
    with no marker written and no chance to ignore it -- instead of the
    child's own trap. Gating the first critical sample on ``ready`` makes
    the test's pass/fail independent of how long child startup happens to
    take on the host running it."""

    def _fn():
        return critical_sample if ready.exists() else _HEALTHY_SAMPLE

    return _fn


def test_nudge_should_sigterm_agent_within_one_tick_and_end_tier_nudge_without_escalation_when_run(
    tmp_path,
):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    marker = tmp_path / "got-term"
    ready = tmp_path / "ready"

    rc = serve_cmd._spawn_round(
        _sigterm_trapping_child_argv(marker, ready),
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(
            pressure=_HostHealthPressureConfig(in_round_nudge=True)
        ),
        clock=PollLoopClock(),
        sample_fn=_sample_fn_once_ready(ready, _CRITICAL_SAMPLE),
    )

    assert marker.read_text() == "term"  # the agent child actually received SIGTERM
    assert rc == 0  # ended via the leader's own cooperative exit, not a killpg
    events = read_events_for_current_month(log_dir)
    terminated = [e for e in events if e.get("event") == "round_mem_terminated"]
    assert terminated[0]["tier"] == "nudge"  # fired at the FIRST critical sample (consecutive == 1)
    assert terminated[0]["consecutive"] == 1
    assert [e for e in events if e.get("event") == "round_supervisor_wedged"] == []


def test_nudge_should_not_suppress_the_hard_terminate_when_the_agent_ignores_the_nudge_sigterm(
    tmp_path,
):
    """The double-emit case: the nudge fires at the first critical sample but
    the agent doesn't wrap up, so critical_streak keeps climbing to the hard
    threshold and _terminate_round ALSO fires -- two round_mem_terminated
    events in the one round, but round_was_mem_terminated still reads True
    exactly once (the per-round verdict, not a per-event count)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # round_was_mem_terminated scopes "this round" against the newest
    # round_substrate_before -- _spawn_round itself never emits that event
    # (serve_cmd.cmd does, right before calling it), so stamp it here to
    # exercise the real per-round verdict reader, not just the raw events.
    emit_round_substrate_before(log_dir, round_num=1, git_head="abc", paths_hash="x")
    ready = tmp_path / "ready"

    cfg = MonitorHostHealthConfig(pressure=_HostHealthPressureConfig(in_round_nudge=True))

    rc = serve_cmd._spawn_round(
        _sigterm_ignoring_child_argv(ready),
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=cfg,
        clock=PollLoopClock(),
        sample_fn=_sample_fn_once_ready(ready, _CRITICAL_SAMPLE),
    )

    assert rc != 0  # reaped via killpg -- the agent never cooperated with either SIGTERM

    events = read_events_for_current_month(log_dir)
    terminated = [e for e in events if e.get("event") == "round_mem_terminated"]
    assert len(terminated) == 2
    assert terminated[0]["tier"] == "nudge"
    assert terminated[0]["consecutive"] == 1
    assert terminated[1]["tier"] == "terminate"
    assert terminated[1]["consecutive"] >= cfg.pressure.critical_consecutive_samples
    assert [e for e in events if e.get("event") == "round_supervisor_wedged"] == []

    # The per-round boolean is not double-counted by the two events.
    assert round_was_mem_terminated(log_dir) is True
