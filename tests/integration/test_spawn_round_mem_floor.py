"""Group 3 action half, part 2: the mid-round hard floor. A pre-round gate alone
cannot stop a single round that balloons mid-flight (metrics only sample at
round boundaries), so _spawn_round's mid-round wait_exit loop also samples
host_health roughly every ~10s.

0.2.16 requires SUSTAINED critical pressure before _terminate_round's the
round: a `critical_streak` counter increments on a critical verdict and
resets to 0 on any non-critical one, and only crossing
`pressure.critical_consecutive_samples` (3 by default) in a row terminates -- a
single spike must not kill a round; only sustained coma-onset does.

0.2.16 also fixed the swap leg's `prev` sample. 0.2.15 pinned the round-start
sample as `prev` for the whole round, making the tier-2 swap delta CUMULATIVE
since round-start: once it crossed the noise floor it could never fall back
below it, so the new hysteresis streak could never reset for that leg and
every long round eventually died on it regardless of whether the host had
recovered (a prevent-swapping argument, against the unresponsiveness-only
north star). The floor now passes the PREVIOUS TICK's sample as `prev`, so
the swap delta is a per-interval rate that resets every ~10s tick and can
fall back below the noise floor -- see
`test_spawn_round_should_not_terminate_when_swap_streak_resets_after_single_tick_jump`.

`test_spawn_round_should_not_terminate_when_slow_swap_trickle_stays_below_per_tick_noise_floor`
below covers the original field bug (a PSI-off host has no other path to
critical) under the new per-tick semantics: a slow, sustained trickle no
longer crosses the floor at all (that host now relies on Task 1's
config-tunable PSI thresholds, or a lowered `memory.swap_out_noise_floor_mb`,
instead of the reverted cumulative accounting).

Mirrors test_spawn_round_wedged.py's shape (real subprocess, TERM-first path)
but with an injected clock that fakes elapsed wall time so the test does not
block for a real ~10s interval."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from agent_runner import _procwait
from agent_runner.cli import serve_cmd
from agent_runner.config import (
    MonitorHostHealthConfig,
    _HostHealthBrakeConfig,
    _HostHealthMemoryConfig,
    _HostHealthPressureConfig,
)
from tests._test_helpers import read_events_for_current_month


@pytest.fixture(autouse=True)
def _fallback_wait_exit(monkeypatch):
    """Every test below drives _spawn_round's mid-round loop by sample_fn
    CALL COUNT (via the sentinel-waiting children + _TickingClock), never by
    real wall time. _spawn_round's mid-round wait is now one wait_exit call;
    its fast path is a real select/kqueue registration that blocks in real
    wall-clock and cannot be driven by a fake clock (see _procwait's module
    docstring), so force the poll FALLBACK instead, and shrink its real
    per-tick cadence from production's 1s to 0.01s -- the direct
    replacement for the old proc.wait(timeout=_ROUND_POLL_TICK_S) shrink.
    This is the one file in the suite allowed to do that;
    test_spawn_round_wedged.py keeps the real fast path + real 1s tick as
    the sole real-tick/real-TERM path."""
    monkeypatch.setattr(_procwait, "exit_fd", lambda proc: None)
    monkeypatch.setattr(_procwait, "_POLL_TICK_S", 0.01)


def _sentinel_child_argv(sentinel: Path) -> list[str]:
    """A round-leader child that idles until ``sentinel`` exists, then exits
    0 -- lets a test's sample_fn decide exactly how many mid-round ticks the
    round survives (call-count-driven) instead of pinning a real
    ``time.sleep(N)`` that either outruns or idles past the fast test tick."""
    return [
        sys.executable,
        "-c",
        "import pathlib, time\n"
        f"p = pathlib.Path({str(sentinel)!r})\n"
        "while not p.exists():\n"
        "    time.sleep(0.01)\n",
    ]


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


def _slow_swap_sample_fn(sentinel: Path, stop_after: int = 6):
    """PSI unreadable, MemAvailable inflated at 82MB (comfortably above
    avail_min_mb=40 -- combined-low genuinely cannot fire on MemAvailable
    alone), MemFree critically low (~5MB -- the "actively dying" condition
    critical is gated on), swap_sout climbing only ~10MB per ~10s tick -- a
    realistic SLOW SD/USB swap trickle whose PER-INTERVAL delta never crosses
    the 32 MiB floor. With the 0.2.16 per-tick `prev`, this NEVER reaches
    critical via the swap leg no matter how many ticks elapse (the reverted
    0.2.15 behavior made it cross cumulatively; see the module docstring).

    Touches ``sentinel`` once ``stop_after`` ticks have been sampled --
    comfortably past the 5th tick where the OLD cumulative accounting would
    have crossed the 32 MiB floor -- so the sentinel-waiting round leader
    (see ``_sentinel_child_argv``) exits promptly once the discriminator has
    genuinely been exercised, instead of running a fixed real
    ``time.sleep``."""
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        if calls["n"] >= stop_after:
            sentinel.touch()
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
    elapses without the test actually waiting ~10 real seconds. sleep() is a
    REAL (short) block, deliberately NOT advancing the fake monotonic time --
    it is the poll FALLBACK's own per-tick pacing (shrunk to 0.01s by this
    file's autouse fixture), the same real role production's old
    proc.wait(timeout=_ROUND_POLL_TICK_S) played: give the real round-leader
    subprocess repeated real chances to actually exit between ticks, so a
    fast-exiting child is noticed before the fake clock ever races ahead to
    a mem-check crossing that hasn't really happened yet."""

    def __init__(self, step: float = 5.0):
        self._t = 0.0
        self._step = step

    def monotonic(self) -> float:
        self._t += self._step
        return self._t

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def test_spawn_round_should_terminate_and_emit_events_when_psi_critical_pressure_sustained(
    tmp_path,
):
    """PSI-based critical pressure exercises the mechanism (sampling cadence,
    terminate-and-emit, distinctness from round_supervisor_wedged) -- see
    test_spawn_round_should_not_terminate_when_slow_swap_trickle_stays_below_per_tick_noise_floor
    below for the actual field-bug coverage (that host has no PSI at all)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,  # large enough that the mem floor trips first, not the ceiling
        round_num=1,
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

    # 0.2.16 Task 4: the streak + Pressure.context ride along so an operator
    # can retune host_health thresholds from the event stream alone.
    assert terminated[0]["consecutive"] == 3
    assert terminated[0]["context"]["psi_full_avg10"] == 70.0
    assert terminated[0]["tier"] == "terminate"

    # The calibration signal: every critical tick emits round_mem_critical_sample
    # up to the 2x pressure.critical_consecutive_samples cap (0.2.17); this terminate
    # path stops at streak 3, well under the cap, so the full 1 -> 2 -> 3 build-up
    # is visible before the terminate threshold is crossed.
    samples = [e for e in events if e.get("event") == "round_mem_critical_sample"]
    assert [s["consecutive"] for s in samples] == [1, 2, 3]
    assert samples[0]["round_num"] == 1
    assert samples[-1]["context"]["psi_full_avg10"] == 70.0

    # Distinct from the wall-clock-ceiling path: this is a memory-pressure kill,
    # not a wedged-round kill, so round_supervisor_wedged must NOT also fire.
    assert [e for e in events if e.get("event") == "round_supervisor_wedged"] == []


def test_spawn_round_should_not_terminate_when_slow_swap_trickle_stays_below_per_tick_noise_floor(
    tmp_path,
):
    """The original field-bug shape under the 0.2.16 per-tick fix: PSI
    unreadable, MemAvailable inflated at 82MB well above avail_min_mb=40
    (combined-low genuinely cannot fire), MemFree critically low (~5MB),
    swap_out climbing only ~10MB per ~10s tick -- a SLOW SD/USB swap trickle
    whose PER-TICK delta stays below the 32 MiB floor on every single tick.
    0.2.15 pinned the round-start sample as `prev`, so this crossed the floor
    CUMULATIVELY over enough ticks; 0.2.16 reverted that (a transient-spike
    argument, not an unresponsiveness one) so this host now never reaches
    critical via the swap leg at all -- it relies on PSI (Task 1) or a
    lowered memory.swap_out_noise_floor_mb instead."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # stop_after=6 (in _slow_swap_sample_fn) is comfortably past the 5th
    # ~10s tick where the OLD cumulative accounting would have crossed the
    # 32 MiB floor (10MB/tick * 4 deltas = 40MB) -- proving this is a
    # genuine behavior discriminator, not just "too short a run to matter
    # either way". The sentinel-waiting child exits the instant that many
    # ticks have actually been sampled, instead of pinning a real sleep.
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(memory=_HostHealthMemoryConfig(avail_min_mb=40)),
        clock=_TickingClock(),
        sample_fn=_slow_swap_sample_fn(sentinel),
    )

    assert rc == 0  # never terminated -- the round completed on its own

    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "round_mem_terminated"] == []


def test_spawn_round_should_not_terminate_when_host_stays_healthy_across_ticks(tmp_path):
    """The negative control for the cumulative-swap floor: a healthy host with
    ample MemFree (~4000MB) and near-zero cumulative swap-out (constant
    swap_sout) is sampled across several ~10s ticks and never reaches
    critical, so the round runs to its own clean exit -- no round_mem_terminated
    and no round_supervisor_wedged."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)
    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        if calls["n"] >= 3:  # a few ~10s ticks, same intent as the old sleep(3)
            sentinel.touch()
        return _HEALTHY_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(memory=_HostHealthMemoryConfig(avail_min_mb=40)),
        clock=_TickingClock(),
        sample_fn=_sample_fn,
    )

    assert rc == 0  # clean exit, never terminated by the floor

    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "round_mem_terminated"] == []
    assert [e for e in events if e.get("event") == "round_supervisor_wedged"] == []


def test_spawn_round_should_skip_sampling_when_round_finishes_before_first_check_interval(
    tmp_path,
):
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
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(step=0.01),  # never crosses the 10s interval boundary
        sample_fn=lambda: calls.append(1) or _CRITICAL_SAMPLE,
    )

    assert rc == 0
    assert calls == []  # interval never elapsed -- sampler was never invoked
    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "round_mem_terminated"] == []


def test_spawn_round_should_not_terminate_when_single_critical_sample_is_followed_by_healthy_ticks(
    tmp_path,
):
    """Hysteresis: one critical tick then healthy forever after must NOT
    terminate -- the default pressure.critical_consecutive_samples=3 means a
    transient spike (the exact false-positive this floor must not produce)
    never reaches the streak. But the near-miss is still visible: the single
    critical tick emits round_mem_critical_sample with consecutive=1 -- the
    0.2.16 calibration signal an operator would otherwise never see."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)

    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        if calls["n"] >= 2:  # the one critical tick plus a healthy confirmation
            sentinel.touch()
        return _CRITICAL_SAMPLE if calls["n"] == 1 else _HEALTHY_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(),
        sample_fn=_sample_fn,
    )

    assert rc == 0  # never terminated -- the round completed on its own

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert "round_mem_terminated" not in kinds
    samples = [e for e in events if e.get("event") == "round_mem_critical_sample"]
    assert [s["consecutive"] for s in samples] == [1]  # the near-miss, still visible


def test_spawn_round_should_not_terminate_when_swap_streak_resets_after_single_tick_jump(
    tmp_path,
):
    """THE critical swap-leg fix: swap_sout jumps once (one tick's delta above
    the noise floor) then goes flat (per-tick delta back to 0) while mem_free
    stays low. With a CUMULATIVE round-start prev the delta would never fall
    back below the floor once crossed, so the streak could never reset and a
    long round would eventually die on this leg regardless of whether the
    host recovered. With a PER-TICK prev, only the one tick that actually
    jumped is critical -- the streak resets on the very next (flat) tick --
    so the round is NOT terminated."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)

    jump = MonitorHostHealthConfig().memory.swap_out_noise_floor_mb * 1024 * 1024 + 1
    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        if calls["n"] >= 4:  # jump tick + a couple of flat confirmation ticks
            sentinel.touch()
        # tick 1: baseline (0). tick 2: one big jump (delta vs tick 1's
        # baseline crosses the noise floor). tick 3+: flat at the jumped
        # value (per-tick delta back to 0). avail_min_mb is set below the
        # mem_available reading so tier 3 (combined-low) can't fire either --
        # PSI unreadable, so tier 2 (swap) is the only path to a verdict.
        sout = jump if calls["n"] >= 2 else 0
        return {
            "psi_some_avg10": None,
            "psi_full_avg10": None,
            "mem_free_mb": 5,  # critically low -- "actively dying" gate
            "mem_available_mb": 5,
            "swap_sout": sout,
        }

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(memory=_HostHealthMemoryConfig(avail_min_mb=1)),
        clock=_TickingClock(),
        sample_fn=_sample_fn,
    )

    assert rc == 0  # never terminated -- the round completed on its own

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert "round_mem_terminated" not in kinds


def test_spawn_round_should_not_terminate_when_in_round_terminate_is_disabled(tmp_path):
    """in_round_terminate=False: sustained critical pressure must NEVER
    _terminate_round -- the loop keeps sampling (so a future re-enable or
    observability layer still sees the signal) but the kill switch is off."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)
    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        if calls["n"] >= 4:  # several sustained-critical ticks with the switch off
            sentinel.touch()
        return _CRITICAL_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(
            pressure=_HostHealthPressureConfig(in_round_terminate=False)
        ),
        clock=_TickingClock(),
        sample_fn=_sample_fn,
    )

    assert rc == 0  # never terminated despite sustained critical pressure

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert "round_mem_terminated" not in kinds


def test_spawn_round_should_cap_critical_sample_events_and_resume_after_streak_reset(tmp_path):
    """0.2.17: round_mem_critical_sample is capped at
    2 * pressure.critical_consecutive_samples (1..6 at the default 3) -- a
    sustained-critical don't-terminate run (here: the off switch) must not
    keep writing an event on EVERY tick for up to a whole round_budget_s on
    a permanently-deferred/off host. The streak itself (critical_streak, and
    _mid_round_action's threshold check) is untouched by the cap -- only
    whether the calibration event fires. A non-critical tick still resets
    the streak to 0, so sampling resumes from 1 on the next critical run --
    the cap is per streak-episode, not a one-shot lifetime limit."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # 8 critical ticks, 2 healthy ticks, then one more critical tick (11) --
    # comfortably past the cap (6) on both sides of the reset. Ticks beyond
    # 11 clamp back to healthy (rather than staying critical indefinitely,
    # as production would): the sentinel is touched exactly at 11, but a
    # couple of extra ticks may still land before the round leader notices
    # it and exits, and this test's own assertions are exact-equality on
    # `consecutive` -- clamping keeps any such overshoot a no-op (a healthy
    # tick resets the streak and emits nothing) instead of resuming the
    # critical run past 6 again and re-tripping the cap assertions below.
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)

    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        n = calls["n"]
        if n >= 11:
            sentinel.touch()
        if n <= 8:
            return _CRITICAL_SAMPLE
        if n == 11:
            return _CRITICAL_SAMPLE
        return _HEALTHY_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        # Comfortably larger than the other tests' 300: this is the longest
        # tick run in the file (11 ticks + possible overshoot), and the
        # fallback wait_exit loop reads clock.monotonic() more often per
        # tick than the old proc.wait loop did (once inside wait_exit's own
        # poll, on top of the outer deadline/next_mem_check checks) -- pure
        # _TickingClock virtual-time bookkeeping, unrelated to the streak
        # decision under test, but it burns through a tight budget faster.
        timeout_s=3000,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(
            pressure=_HostHealthPressureConfig(in_round_terminate=False)
        ),
        clock=_TickingClock(),
        sample_fn=_sample_fn,
    )

    assert rc == 0  # off switch: never terminated despite sustained critical pressure

    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "round_mem_terminated"] == []
    samples = [e for e in events if e.get("event") == "round_mem_critical_sample"]
    consecutive = [s["consecutive"] for s in samples]

    # First sustained-critical run: streak 1..6 fires, then the cap silences
    # ticks 7 and 8 (both still critical -- the underlying streak keeps
    # counting, but the calibration event stops).
    assert consecutive[:6] == [1, 2, 3, 4, 5, 6]
    assert 7 not in consecutive
    assert 8 not in consecutive
    # A non-critical tick (calls 9-10) resets the streak -- the very next
    # critical tick resumes sampling from 1, not from where it left off.
    assert consecutive[6] == 1


def test_spawn_round_should_emit_nothing_when_off_switch_overrides_cgroup_defer(tmp_path):
    """The one cell _mid_round_action's own unit test proves structurally but
    no integration test drove end to end: in_round_terminate=False AND
    defer_to_cgroup=True at once. The off switch wins over cgroup-defer (see
    _mid_round_action's docstring: "count_only" is returned before
    defer_to_cgroup is even consulted), so sustained critical pressure here
    must produce NEITHER round_mem_terminated NOR
    mem_pressure_deferred_to_cgroup -- unlike
    test_spawn_round_should_defer_to_cgroup_when_memory_and_swap_both_bounded
    (defer_to_cgroup=True alone, default in_round_terminate=True), which
    DOES emit mem_pressure_deferred_to_cgroup. If count_only's off-switch
    check were ever weakened to fall through to the defer/terminate branch
    when defer_to_cgroup is True, this test would start seeing
    mem_pressure_deferred_to_cgroup and fail."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)
    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        if calls["n"] >= 3:
            sentinel.touch()
        return _CRITICAL_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(
            pressure=_HostHealthPressureConfig(in_round_terminate=False)
        ),
        clock=_TickingClock(),
        sample_fn=_sample_fn,
        defer_to_cgroup=True,
    )

    assert rc == 0  # never terminated -- the round completed on its own

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert "round_mem_terminated" not in kinds
    assert "mem_pressure_deferred_to_cgroup" not in kinds
    # Sustained critical pressure genuinely reached the floor -- this is not
    # a vacuous pass from a round too short to ever sample.
    assert kinds.count("round_mem_critical_sample") >= 1


def test_spawn_round_should_defer_to_cgroup_when_memory_and_swap_both_bounded(tmp_path):
    """0.2.16 Task 3: when the cgroup's (mem+swap) budget is bounded end to
    end (both memory.max and memory.swap.max finite -- exactly the field
    host's MemoryMax=320M + MemorySwapMax=160M), kernel cgroup-OOM WILL fire
    and contain the agent while the host stays responsive, so the cruder
    host-wide floor steps back: sustained critical pressure (even with the
    default in_round_terminate=True) emits mem_pressure_deferred_to_cgroup
    instead of terminating -- defer_to_cgroup OVERRIDES the off switch's
    normal True meaning. 0.2.16 Task 4: round_mem_critical_sample still fires
    per critical tick in this mode -- the streak/context stays useful
    calibration signal independent of the terminate-vs-defer choice."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)
    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        if calls["n"] >= 4:
            sentinel.touch()
        return _CRITICAL_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(),
        sample_fn=_sample_fn,
        defer_to_cgroup=True,
    )

    assert rc == 0  # round completed on its own -- the floor deferred, never terminated

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert kinds.count("mem_pressure_deferred_to_cgroup") >= 1
    assert kinds.count("round_mem_critical_sample") >= 1
    assert "round_mem_terminated" not in kinds
    assert "round_supervisor_wedged" not in kinds


def test_spawn_round_should_terminate_when_defer_to_cgroup_is_false(tmp_path):
    """Only memory.max finite (systemd's MemoryMax-without-MemorySwapMax
    default -- swap unbounded) means cgroup-OOM never fires on its own (the
    agent just swaps), so defer_to_cgroup is False and the floor stays
    armed: sustained critical pressure still terminates exactly as T2 did."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(),
        sample_fn=lambda: _CRITICAL_SAMPLE,
        defer_to_cgroup=False,
    )

    assert rc != 0  # terminated, not a clean exit

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert kinds.count("round_mem_terminated") == 1
    assert "mem_pressure_deferred_to_cgroup" not in kinds


_WARNING_SAMPLE = {
    "psi_some_avg10": 10.0,  # >= some_avg10_warning (5.0), < full_avg10_critical (60.0)
    "psi_full_avg10": 10.0,
    "mem_free_mb": 4000,
    "mem_available_mb": 4000,
    "swap_sout": 0,
}


def test_spawn_round_should_engage_and_restore_soft_brake_when_warning_sustained_and_armed(
    tmp_path, monkeypatch
):
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "go"
    calls = {"engage": 0, "restore": []}

    def _fake_engage(*, step_pct):
        calls["engage"] += 1
        sentinel.touch()  # let the sentinel-child leader exit right after engage
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        calls["restore"].append(previous)
        return True

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    rc = serve_cmd._spawn_round(
        _sentinel_child_argv(sentinel),
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=_TickingClock(),
        sample_fn=lambda: _WARNING_SAMPLE,
    )

    assert rc == 0
    assert calls["engage"] == 1
    assert calls["restore"] == ["max"]  # finally restored the stashed token
    events = read_events_for_current_month(log_dir)
    engaged = [e for e in events if e.get("event") == "memory_high_engaged"]
    released = [e for e in events if e.get("event") == "memory_high_released"]
    assert len(engaged) == 1 and engaged[0]["written"] == 999
    assert released and released[0]["reason"] == "round_end"


def test_spawn_round_should_not_engage_or_crash_when_warning_threshold_is_zero_and_pressure_absent(
    tmp_path, monkeypatch
):
    """Review Minor 1 regression: warning_streak resets to 0 on a no-pressure
    tick, so `warning_streak >= warning_consecutive_samples` is trivially true
    every tick when a directly-constructed config (bypassing the TOML parser's
    _require_positive_int validation) sets warning_consecutive_samples=0. Without
    the engage guard's own `pressure is not None` conjunct, this would engage
    the brake on a HEALTHY sample and then crash reading None.signal/.context --
    a fail-CLOSED crash on the reap-adjacent path. Must neither engage nor crash."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "exit.sentinel"
    argv = _sentinel_child_argv(sentinel)
    calls = {"engage": 0}

    def _fake_engage(*, step_pct):
        calls["engage"] += 1
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=0)
    )
    calls_n = {"n": 0}

    def _sample_fn():
        calls_n["n"] += 1
        if calls_n["n"] >= 3:  # a few no-pressure ticks with the zeroed threshold
            sentinel.touch()
        return _HEALTHY_SAMPLE  # memory_pressure() returns None for this sample

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=_TickingClock(),
        sample_fn=_sample_fn,
    )

    assert rc == 0  # completed cleanly -- no AttributeError crash
    assert calls["engage"] == 0  # never engaged on a no-pressure tick


def test_spawn_round_should_restore_soft_brake_when_terminated_by_sustained_critical_pressure(
    tmp_path, monkeypatch
):
    """Review Minor 3 coverage: the "terminate"-verdict return is a SEPARATE
    exit point inside the try from the clean-exit return already covered above
    -- prove the finally's restore runs there too."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    calls = {"restore": []}

    def _fake_engage(*, step_pct):
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        calls["restore"].append(previous)
        return True

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )
    calls_n = {"n": 0}

    def _sample_fn():
        calls_n["n"] += 1
        # 3 warning ticks engage the brake, then sustained critical pressure
        # (default critical_consecutive_samples=3) hard-terminates the round.
        return _WARNING_SAMPLE if calls_n["n"] <= 3 else _CRITICAL_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=_TickingClock(),
        sample_fn=_sample_fn,
    )

    assert rc != 0  # terminated, not a clean exit
    assert calls["restore"] == ["max"]
    events = read_events_for_current_month(log_dir)
    assert len([e for e in events if e.get("event") == "round_mem_terminated"]) == 1
    released = [e for e in events if e.get("event") == "memory_high_released"]
    assert released and released[0]["reason"] == "round_end"


def test_spawn_round_should_restore_brake_and_propagate_original_exception_when_body_raises(
    tmp_path, monkeypatch
):
    """Review Minor 2 + Minor 3 coverage together: the except-BaseException
    unwind path also restores the brake via the finally, AND a shielded emit
    failure during that cleanup must never replace the ORIGINAL propagating
    exception with an emit error."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    calls = {"restore": []}

    def _fake_engage(*, step_pct):
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        calls["restore"].append(previous)
        return True

    def _raise_on_release(*_a, **_k):
        raise OSError("disk full during emit -- must not mask the original exception")

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    monkeypatch.setattr(_serve_cgroup, "emit_memory_high_released", _raise_on_release)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=1)
    )
    calls_n = {"n": 0}

    def _sample_fn():
        calls_n["n"] += 1
        if calls_n["n"] >= 2:
            raise RuntimeError("the original round-body exception")
        return _WARNING_SAMPLE  # tick 1: engages the brake (threshold=1)

    with pytest.raises(RuntimeError, match="the original round-body exception"):
        serve_cmd._spawn_round(
            argv,
            log_dir / "round-1.log",
            {},
            timeout_s=300,
            round_num=1,
            host_health_cfg=hh,
            clock=_TickingClock(),
            sample_fn=_sample_fn,
        )

    assert calls["restore"] == ["max"]  # cleanup still ran despite the shielded emit raising


def test_spawn_round_should_emit_write_failed_when_restore_itself_fails(tmp_path, monkeypatch):
    """Review Minor 3 coverage, updated for fix-wave Minor 4: restore_leaf_memory_high
    returning False (a restore-time OSError, fail-open) must emit memory_high_write_failed
    with errno=None -- never the misleading errno=0 (reads as POSIX success), since the
    finally has no real errno to report (restore_leaf_memory_high swallows it into a bare
    bool) -- and never memory_high_released."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "go"

    def _fake_engage(*, step_pct):
        sentinel.touch()
        return {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        }

    def _fake_restore(previous, **_k):
        return False  # simulates a restore-time OSError, fail-open

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    rc = serve_cmd._spawn_round(
        _sentinel_child_argv(sentinel),
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=_TickingClock(),
        sample_fn=lambda: _WARNING_SAMPLE,
    )

    assert rc == 0
    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "memory_high_released"] == []
    failed = [e for e in events if e.get("event") == "memory_high_write_failed"]
    assert failed and failed[0]["errno"] is None


def test_spawn_round_should_carry_the_real_errno_when_engage_itself_fails(tmp_path, monkeypatch):
    """Fix-wave Minor 4 counterpart: the engage-fail site (a real OSError from
    metrics.engage_leaf_memory_high's write) must keep forwarding the real
    errno unchanged -- only the restore-fail sites (which never see a real
    errno) switched to the None sentinel."""
    import errno as errno_mod

    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "go"

    def _fake_engage(*, step_pct):
        sentinel.touch()
        return {"engaged": False, "errno": errno_mod.EACCES}

    monkeypatch.setattr(metrics, "engage_leaf_memory_high", _fake_engage)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    rc = serve_cmd._spawn_round(
        _sentinel_child_argv(sentinel),
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=_TickingClock(),
        sample_fn=lambda: _WARNING_SAMPLE,
    )

    assert rc == 0
    events = read_events_for_current_month(log_dir)
    failed = [e for e in events if e.get("event") == "memory_high_write_failed"]
    assert failed and failed[0]["errno"] == errno_mod.EACCES


def test_spawn_round_should_latch_recovery_restore_failure_instead_of_flooding_each_healthy_tick(
    tmp_path, monkeypatch
):
    """Fix-wave Minor 2: when the recovery-path restore keeps failing, the
    engage path already had brake_disarmed to stop retry-and-re-emit every
    tick; the recovery path had no equivalent, so none_streak kept growing
    past the threshold and memory_high_write_failed re-fired on EVERY
    subsequent healthy tick (~one per 10s) for the rest of the round -- a
    flood. brake_restore_failed latches after the first recovery-path
    failure. The finally at round end is a separate, distinct restore
    attempt and may add exactly one more emission -- that one is NOT part
    of the flood this fix removes."""
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "go"
    seq = {"n": 0}
    restore_calls = {"n": 0}

    def _sample():
        seq["n"] += 1
        if seq["n"] <= 3:
            return _WARNING_SAMPLE  # 3 warning ticks -> engage
        if seq["n"] >= 9:
            sentinel.touch()  # a few failing-restore healthy ticks past the threshold
        return _HEALTHY_SAMPLE

    def _fake_restore(previous, **_k):
        restore_calls["n"] += 1
        return False  # a persistently-failing restore, fail-open

    monkeypatch.setattr(
        metrics,
        "engage_leaf_memory_high",
        lambda *, step_pct: {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        },
    )
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    rc = serve_cmd._spawn_round(
        _sentinel_child_argv(sentinel),
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=_TickingClock(),
        sample_fn=_sample,
    )

    assert rc == 0
    # exactly 2 restore attempts: the ONE latched recovery attempt (tick 6, when
    # none_streak first reaches the threshold) + the finally's own attempt at
    # round end -- never one per healthy tick (6 healthy ticks ran: 4..9).
    assert restore_calls["n"] == 2
    events = read_events_for_current_month(log_dir)
    failed = [e for e in events if e.get("event") == "memory_high_write_failed"]
    assert len(failed) == 2
    assert all(e["errno"] is None for e in failed)


def test_spawn_round_should_release_soft_brake_when_warning_clears_for_n_ticks(
    tmp_path, monkeypatch
):
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "go"
    seq = {"n": 0}
    restores = []

    def _sample():
        seq["n"] += 1
        return _WARNING_SAMPLE if seq["n"] <= 3 else _HEALTHY_SAMPLE  # 3 warn engage, then heal

    def _fake_restore(previous, **_k):
        restores.append(previous)
        sentinel.touch()  # let the leader exit once the recovery release has fired
        return True

    monkeypatch.setattr(
        metrics,
        "engage_leaf_memory_high",
        lambda *, step_pct: {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        },
    )
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", _fake_restore)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    serve_cmd._spawn_round(
        _sentinel_child_argv(sentinel),
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=_TickingClock(),
        sample_fn=_sample,
    )

    events = read_events_for_current_month(log_dir)
    released = [e for e in events if e.get("event") == "memory_high_released"]
    assert any(e["reason"] == "recovered" for e in released)


def test_spawn_round_should_hold_soft_brake_when_a_critical_sample_follows_engage(
    tmp_path, monkeypatch
):
    from agent_runner import metrics
    from agent_runner.cli import _serve_cgroup

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    sentinel = tmp_path / "go"
    seq = {"n": 0}

    def _sample():
        seq["n"] += 1
        if seq["n"] <= 3:
            return _WARNING_SAMPLE  # engage
        if seq["n"] == 4:
            sentinel.touch()  # end the round on the critical tick
            return _CRITICAL_SAMPLE  # a critical sample must NOT release the brake
        return _CRITICAL_SAMPLE

    monkeypatch.setattr(
        metrics,
        "engage_leaf_memory_high",
        lambda *, step_pct: {
            "engaged": True,
            "previous": "max",
            "written": 999,
            "memory_current": 300 * 1024 * 1024,
        },
    )
    monkeypatch.setattr(metrics, "restore_leaf_memory_high", lambda p, **_k: True)
    _serve_cgroup._BRAKE_ARMED_BY_LOG_DIR[log_dir] = True

    hh = MonitorHostHealthConfig(
        brake=_HostHealthBrakeConfig(memory_high=True, warning_consecutive_samples=3)
    )

    serve_cmd._spawn_round(
        _sentinel_child_argv(sentinel),
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        round_num=1,
        host_health_cfg=hh,
        clock=_TickingClock(),
        sample_fn=_sample,
    )

    events = read_events_for_current_month(log_dir)
    released = [e for e in events if e.get("event") == "memory_high_released"]
    # NO "recovered" -- held through critical
    assert [e["reason"] for e in released] == ["round_end"]


def test_spawn_round_should_skip_sampling_when_host_health_cfg_is_none(tmp_path):
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
        round_num=1,
        sample_fn=lambda: calls.append(1) or _CRITICAL_SAMPLE,
    )

    assert rc == 0
    assert calls == []
