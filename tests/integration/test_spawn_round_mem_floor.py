"""Group 3 action half, part 2: the mid-round hard floor. A pre-round gate alone
cannot stop a single round that balloons mid-flight (metrics only sample at
round boundaries), so _spawn_round's existing 1s proc.wait loop also samples
host_health roughly every ~10s.

0.2.16 requires SUSTAINED critical pressure before _terminate_round's the
round: a `critical_streak` counter increments on a critical verdict and
resets to 0 on any non-critical one, and only crossing
`mem_critical_consecutive_samples` (3 by default) in a row terminates -- a
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
`test_swap_leg_streak_resets_with_per_tick_prev`.

`test_given_cache_poor_psi_off_host_*` below covers the original field bug
(a PSI-off host has no other path to critical) under the new per-tick
semantics: a slow, sustained trickle no longer crosses the floor at all
(that host now relies on Task 1's config-tunable PSI thresholds, or a
lowered `swap_sout_noise_floor_mb`, instead of the reverted cumulative
accounting).

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
    the 32 MiB floor. With the 0.2.16 per-tick `prev`, this NEVER reaches
    critical via the swap leg no matter how many ticks elapse (the reverted
    0.2.15 behavior made it cross cumulatively; see the module docstring)."""
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

    # 0.2.16 Task 4: the streak + Pressure.context ride along so an operator
    # can retune host_health thresholds from the event stream alone.
    assert terminated[0]["consecutive"] == 3
    assert terminated[0]["context"]["psi_full_avg10"] == 70.0

    # The calibration signal: EVERY critical tick emits round_mem_critical_sample
    # (not deduped), so the streak building 1 -> 2 -> 3 is visible even before
    # the terminate threshold is crossed.
    samples = [e for e in events if e.get("event") == "round_mem_critical_sample"]
    assert [s["consecutive"] for s in samples] == [1, 2, 3]
    assert samples[0]["round_num"] == 1
    assert samples[-1]["context"]["psi_full_avg10"] == 70.0

    # Distinct from the wall-clock-ceiling path: this is a memory-pressure kill,
    # not a wedged-round kill, so round_supervisor_wedged must NOT also fire.
    assert [e for e in events if e.get("event") == "round_supervisor_wedged"] == []


def test_given_cache_poor_psi_off_host_slow_swap_per_tick_never_terminates(tmp_path):
    """The original field-bug shape under the 0.2.16 per-tick fix: PSI
    unreadable, MemAvailable inflated at 82MB well above mem_avail_min_mb=40
    (combined-low genuinely cannot fire), MemFree critically low (~5MB),
    swap_out climbing only ~10MB per ~10s tick -- a SLOW SD/USB swap trickle
    whose PER-TICK delta stays below the 32 MiB floor on every single tick.
    0.2.15 pinned the round-start sample as `prev`, so this crossed the floor
    CUMULATIVELY over enough ticks; 0.2.16 reverted that (a transient-spike
    argument, not an unresponsiveness one) so this host now never reaches
    critical via the swap leg at all -- it relies on PSI (Task 1) or a
    lowered swap_sout_noise_floor_mb instead."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Long enough that the OLD cumulative accounting would have crossed the
    # 32 MiB floor by the 5th ~10s tick (10MB/tick * 4 deltas = 40MB) --
    # proving this is a genuine behavior discriminator, not just "too short
    # a run to matter either way".
    argv = [sys.executable, "-c", "import time; time.sleep(8)"]

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        host_health_cfg=MonitorHostHealthConfig(mem_avail_min_mb=40),
        clock=_TickingClock(),
        sample_fn=_slow_swap_sample_fn(),
    )
    assert rc == 0  # never terminated -- the round completed on its own

    events = read_events_for_current_month(log_dir)
    assert [e for e in events if e.get("event") == "round_mem_terminated"] == []


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


def test_single_critical_sample_does_not_terminate(tmp_path):
    """Hysteresis: one critical tick then healthy forever after must NOT
    terminate -- the default mem_critical_consecutive_samples=3 means a
    transient spike (the exact false-positive this floor must not produce)
    never reaches the streak. But the near-miss is still visible: the single
    critical tick emits round_mem_critical_sample with consecutive=1 -- the
    0.2.16 calibration signal an operator would otherwise never see."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(3)"]  # exits on its own

    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        return _CRITICAL_SAMPLE if calls["n"] == 1 else _HEALTHY_SAMPLE

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
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


def test_three_consecutive_critical_samples_terminate(tmp_path):
    """Sustained critical (>= mem_critical_consecutive_samples=3 default in a
    row) DOES terminate -- the hysteresis floor still catches the real
    coma-onset case, just not on a single blip."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(),
        sample_fn=lambda: _CRITICAL_SAMPLE,
    )
    assert rc != 0

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert kinds.count("round_mem_terminated") == 1


def test_swap_leg_streak_resets_with_per_tick_prev(tmp_path):
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
    argv = [sys.executable, "-c", "import time; time.sleep(5)"]  # exits on its own

    jump = MonitorHostHealthConfig().swap_sout_noise_floor_mb * 1024 * 1024 + 1
    calls = {"n": 0}

    def _sample_fn():
        calls["n"] += 1
        # tick 1: baseline (0). tick 2: one big jump (delta vs tick 1's
        # baseline crosses the noise floor). tick 3+: flat at the jumped
        # value (per-tick delta back to 0). mem_avail_min_mb is set below the
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
        host_health_cfg=MonitorHostHealthConfig(mem_avail_min_mb=1),
        clock=_TickingClock(),
        sample_fn=_sample_fn,
    )
    assert rc == 0  # never terminated -- the round completed on its own

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert "round_mem_terminated" not in kinds


def test_off_switch_never_terminates(tmp_path):
    """in_round_mem_terminate=False: sustained critical pressure must NEVER
    _terminate_round -- the loop keeps sampling (so a future re-enable or
    observability layer still sees the signal) but the kill switch is off."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(3)"]  # exits on its own

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        host_health_cfg=MonitorHostHealthConfig(in_round_mem_terminate=False),
        clock=_TickingClock(),
        sample_fn=lambda: _CRITICAL_SAMPLE,
    )
    assert rc == 0  # never terminated despite sustained critical pressure

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert "round_mem_terminated" not in kinds


def test_both_finite_defers_never_terminates(tmp_path):
    """0.2.16 Task 3: when the cgroup's (mem+swap) budget is bounded end to
    end (both memory.max and memory.swap.max finite -- exactly the field
    host's MemoryMax=320M + MemorySwapMax=160M), kernel cgroup-OOM WILL fire
    and contain the agent while the host stays responsive, so the cruder
    host-wide floor steps back: sustained critical pressure (even with the
    default in_round_mem_terminate=True) emits mem_pressure_deferred_to_cgroup
    instead of terminating -- defer_to_cgroup OVERRIDES the off switch's
    normal True meaning. 0.2.16 Task 4: round_mem_critical_sample still fires
    per critical tick in this mode -- the streak/context stays useful
    calibration signal independent of the terminate-vs-defer choice."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    argv = [sys.executable, "-c", "import time; time.sleep(5)"]

    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=300,
        host_health_cfg=MonitorHostHealthConfig(),
        clock=_TickingClock(),
        sample_fn=lambda: _CRITICAL_SAMPLE,
        defer_to_cgroup=True,
    )
    assert rc == 0  # round completed on its own -- the floor deferred, never terminated

    events = read_events_for_current_month(log_dir)
    kinds = [e.get("event") for e in events]
    assert kinds.count("mem_pressure_deferred_to_cgroup") >= 1
    assert kinds.count("round_mem_critical_sample") >= 1
    assert "round_mem_terminated" not in kinds
    assert "round_supervisor_wedged" not in kinds


def test_swap_unbounded_still_terminates(tmp_path):
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
