"""Gated real-cgroup pre-OOM property (v0.3.11 Component D-2).

THE PROPERTY: agent-runner TERMINATES + REAPS a memory-pressuring agent when
HOST-wide swap/PSI crosses the calibration, on a real host, BEFORE the host
becomes unresponsive -- in the NON-deferring cgroup shape (see
``conftest.pi_pre_oom_unit`` for why ``MemorySwapMax`` must stay unbounded).
This is NOT a cgroup ``memory.events.oom_kill`` race: the leaf's own OOM
killer staying silent (0) while the host survives is part of the property,
not the mechanism that proves it.

Gated behind ``AGENT_RUNNER_E2E_PI`` (via the ``pi_session`` fixture chain,
reached transitively through ``pi_pre_oom_unit`` -> ``pi_pre_oom_config`` ->
``pi_workdir`` -> ``pi_session``) -- skips cleanly when unset, and is never
run in CI. The real run against a live constrained host is a release
go/no-go step, executed by hand over ``ssh pi``, not by this task.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from .conftest import _ssh

# Ceiling for the whole property-wait loop. The growth child paces itself at
# ~8 MB/2.5s; crossing the 150M MemoryMax, then sustaining critical host
# swap/PSI pressure for 3 consecutive ~10s mid-round ticks
# (_HostHealthPressureConfig.critical_consecutive_samples, the shipped
# default), takes real wall-clock minutes -- generous on purpose so a slow
# real run isn't cut off before the property gets a chance to fire.
_WAIT_TIMEOUT_S = 900
_POLL_INTERVAL_S = 5.0
# Grace to observe the terminated process tree actually vanish (TERM -> grace
# -> killpg, agent_runner.cli._serve_round._terminate_round) after the event
# fires -- reap is not synchronous with the event write.
_REAP_TIMEOUT_S = 20.0


def _wait_until(predicate, timeout_s: float, interval_s: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def _read_events(pi_workdir: str) -> list[dict]:
    """Every event line under this round's log_dir, oldest -> newest, tolerant
    of a not-yet-created events file (round hasn't started ticking yet)."""
    r = _ssh(f"cat {pi_workdir}/logs/events-*.jsonl 2>/dev/null", check=False)
    out: list[dict] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _sample_host_pressure(pi_venv_python: str) -> dict:
    """One host_health-ladder-input sample, via the SAME reader
    (``agent_runner.metrics.sample()``) the property under test relies on --
    reusing it here means "host PSI/swap is elevated" is measured with the
    identical definition agent-runner itself acts on, not a re-derived
    /proc parse that could quietly drift from it."""
    r = _ssh(
        f"{pi_venv_python} -c "
        "'import json, agent_runner.metrics as m; print(json.dumps(m.sample()))'",
        check=False,
    )
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return {}


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_agent_runner_should_terminate_before_host_pressure_peaks_on_real_cgroup(
    pi_pre_oom_unit: dict,
    pi_workdir: str,
    pi_growth_script: str,
    pi_venv_python: str,
) -> None:
    unit = pi_pre_oom_unit["unit"]
    cgroup_path = pi_pre_oom_unit["cgroup_path"]

    # --- Wait for the property (or its weaker sibling) to fire, recording a
    # host-pressure timeseries alongside so a margin can be computed below. ---
    pressure_samples: list[tuple[datetime, dict]] = []
    seen_events: list[dict] = []
    fired: dict | None = None
    outcome: str | None = None
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        pressure_samples.append((datetime.now(UTC), _sample_host_pressure(pi_venv_python)))
        seen_events = _read_events(pi_workdir)
        terminated = [e for e in seen_events if e.get("event") == "round_mem_terminated"]
        engaged = [e for e in seen_events if e.get("event") == "memory_high_engaged"]
        if terminated:
            fired, outcome = terminated[0], "TERMINATE"
            break
        if engaged and outcome is None:
            # The weaker outcome -- keep polling so the hard terminate still
            # gets its full window to also fire (see the go/no-go watch-item
            # below: only TERMINATE is the moat property).
            fired, outcome = engaged[0], "BRAKE_ONLY"
        time.sleep(_POLL_INTERVAL_S)

    kinds_seen = sorted({e.get("event") for e in seen_events})
    print(f"[pre-oom] {len(seen_events)} events captured; kinds={kinds_seen}")

    # go/no-go watch-item (co-residency): serve runs IN the same 150M leaf as
    # the growth child, so its own ~10s sampler could itself be swap-starved.
    # A full-timeout miss with zero critical/brake/terminate signal at all is
    # exactly that failure mode, not a calibration miss -- surface it
    # distinctly from an ordinary assertion message.
    assert fired is not None, (
        f"neither round_mem_terminated nor memory_high_engaged fired within "
        f"{_WAIT_TIMEOUT_S}s. kinds_seen={kinds_seen}. Two distinct failure "
        "modes: (a) the mid-round floor is genuinely not arming/terminating in "
        "time -- a CRITICAL finding, harden calibration/brake-arm/drain timing "
        "before shipping v0.3.11; (b) serve itself is swap-starved in the "
        "150M co-resident leaf and never got to sample -- bump MemoryMax so "
        "the growth child dominates without starving the supervisor, then "
        "re-run. Check host_cgroup_memory_limit's 'defer' field first: True "
        "means the fixture's cgroup shape is wrong (MemorySwapMax not really "
        "unbounded), not (a) or (b)."
    )

    # go/no-go legibility: which outcome fired must be visible, not hidden
    # behind the `or` above -- only the TERMINATE (+ reap) is the moat
    # property; a brake-only engagement is a materially weaker result.
    print(f"MOAT-PROPERTY-OUTCOME: {outcome} event={fired}")
    if outcome == "BRAKE_ONLY":
        print(
            "WATCH-ITEM: only memory_high_engaged fired inside the wait window -- "
            "the soft brake engaged but the hard terminate never did. The "
            "controller must confirm at go/no-go whether the terminate simply "
            "hadn't crossed its sustained-streak threshold yet within this "
            "run's window, or is genuinely inert -- brake-only is NOT the "
            "moat property (it throttles, it does not terminate+reap)."
        )

    # --- Host PSI/swap was elevated at the firing ts: read it straight off
    # the event's own `context` (Pressure.context), the exact reading that
    # triggered this outcome -- no separate re-derivation needed. ---
    context = fired.get("context") or {}
    signal = fired.get("signal")
    assert context, f"{outcome} event carried no context payload: {fired}"
    assert signal in ("psi", "swap_out_rate", "combined_low"), (
        f"{outcome} event has unexpected signal={signal!r}: {fired}"
    )
    print(f"[pre-oom] trigger signal={signal} context={context}")

    # --- Record the margin: terminate/engage ts vs. peak host PSI-full seen
    # during polling, so a shrinking margin across future runs is visible
    # rather than silently absorbed. Informational only -- no baseline exists
    # yet to assert a numeric bound against (this task never executes the
    # real run); see the go/no-go report for the first measured value. ---
    psi_samples = [
        (t, s["psi_full_avg10"]) for t, s in pressure_samples if s.get("psi_full_avg10") is not None
    ]
    if psi_samples:
        peak_dt, peak_val = max(psi_samples, key=lambda p: p[1])
        fired_dt = _parse_ts(fired["ts"])
        margin_s = (peak_dt - fired_dt).total_seconds()
        print(
            f"MARGIN: {outcome} fired at {fired_dt.isoformat()}; host PSI-full peaked "
            f"{peak_val:.1f} at {peak_dt.isoformat()} ({margin_s:+.1f}s relative to the "
            f"{outcome.lower()} -- negative means pressure was still rising when "
            "agent-runner acted, positive means pressure had already crested)"
        )
    else:
        print(
            "MARGIN: no PSI-full samples were collected while polling (PSI "
            "unavailable on this host, or the fixture's sampler probe failed) "
            "-- cannot compute a PSI-based margin for this run"
        )

    # --- The agent tree was reaped -- only meaningful for the TERMINATE
    # branch: the brake throttles, it does not kill the round, so no reap is
    # expected when only memory_high_engaged fired. ---
    if outcome == "TERMINATE":
        reaped = _wait_until(
            lambda: _ssh(f"pgrep -f {pi_growth_script}", check=False).returncode != 0,
            timeout_s=_REAP_TIMEOUT_S,
        )
        assert reaped, (
            "round_mem_terminated fired but the growth-child process tree is "
            f"still running {_REAP_TIMEOUT_S}s later -- reap failed"
        )

    # --- Host stayed responsive: a follow-up ssh still answers. ---
    responsive = _ssh("true", check=False)
    assert responsive.returncode == 0, (
        "host did not answer a follow-up ssh after the property fired -- "
        "possible host-wide unresponsiveness (the exact outcome the moat "
        "property exists to prevent)"
    )

    # --- The leaf's own OOM killer never fired: the child is RAM-bounded and
    # swaps rather than getting cgroup-OOM-killed (a defer-shape symptom, or
    # MemorySwapMax not really unbounded on the live unit). ---
    leaf_events = _ssh(f"sudo cat {cgroup_path}/memory.events", check=False)
    oom_kill = 0
    for line in leaf_events.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "oom_kill":
            oom_kill = int(parts[1])
    assert oom_kill == 0, (
        f"leaf memory.events oom_kill={oom_kill} at {cgroup_path} -- the "
        "cgroup's own OOM killer fired, meaning the child was kernel-OOM-"
        "killed instead of swapping into the (supposedly unbounded) host "
        "swap. Check MemorySwapMax is really 'infinity' on the live unit "
        f"({unit}), not just in the unit file this fixture wrote."
    )
