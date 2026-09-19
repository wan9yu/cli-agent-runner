"""Gated real-cgroup two-arm pre-OOM property (Component D-2 + v0.3.13 control).

TREATMENT: agent-runner TERMINATES + REAPS a memory-pressuring agent when
HOST-wide swap/PSI crosses the calibration, on a real host, BEFORE the host
becomes unresponsive -- in the NON-deferring cgroup shape (see
``conftest.pi_pre_oom_unit`` for why ``MemorySwapMax`` must stay unbounded).
This is NOT a cgroup ``memory.events.oom_kill`` race: the leaf's own OOM
killer staying silent (0) while the host survives is part of the property,
not the mechanism that proves it.

CONTROL: the same unit with only ``MemorySwapMax=0`` (still MemoryHigh=120M,
MemoryMax=150M; no OOMPolicy=, no memory.oom.group). Serve defers the
mid-round floor; the kernel cgroup-OOM contains the capped child. Host stays
up. Do not fuse the arms into an uncapped-before-coma claim.

What treatment proves, and what it does not: under the 150M ``MemoryMax``
safety envelope, the paced growth child's own cgroup-limit memstall dominates
HOST-wide PSI-full, which drives the real
``sample() -> host_health.memory_pressure -> 3-sample streak -> _terminate_round
-> reap`` loop at the PSI rung. So it demonstrates that production loop is live
and calibrated on real hardware under real swap-out load. It deliberately does
NOT exercise an UNCAPPED agent (unsafe on a ~0.5 GB host): the strong
"before the host becomes unresponsive" claim for the uncapped case rests on this
proven loop plus the D-1 calibration ladder -- inferred, not demonstrated here.

Gated behind ``AGENT_RUNNER_E2E_PI`` (pi ssh) and/or ``AGENT_RUNNER_E2E_CGROUP``
(local Linux cgroup v2). Skips cleanly when those flags are unset, and is
never run in CI. The real run against a live constrained host is a release
go/no-go step (local CGROUP first, ArgusPi second), not this task.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

from .conftest import _local_sh, _ssh

# Ceiling for the whole property-wait loop. The growth child paces itself at
# ~8 MB/2.5s; crossing the 150M MemoryMax, then sustaining critical host
# swap/PSI pressure for 3 consecutive ~10s mid-round ticks
# (_HostHealthPressureConfig.critical_consecutive_samples, the shipped
# default), takes real wall-clock minutes -- generous on purpose so a slow
# real run isn't cut off before the property gets a chance to fire.
_WAIT_TIMEOUT_S = 900
# Control fill is MemoryMax-only (150M / 3.2 MB/s ≈ 47s) plus kernel delay.
_CONTROL_WAIT_TIMEOUT_S = 180
_POLL_INTERVAL_S = 5.0
# Shipped default of [monitor.host_health.pressure] critical_consecutive_samples.
_CRITICAL_CONSECUTIVE_SAMPLES = 3
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
    return _read_events_via(_ssh, pi_workdir)


def _read_events_via(run, workdir: str) -> list[dict]:
    """Every event line under this round's log_dir, oldest -> newest, tolerant
    of a not-yet-created events file (round hasn't started ticking yet).

    serve runs as root in the system-scope unit (required -- the host has no
    user cgroup delegation), so its event logs and the log_dir are root-owned;
    read them under ``sudo sh -c`` so both the glob expansion and the cat run
    as root, not as the unprivileged ssh user (which would silently read zero
    events depending on root's umask)."""
    r = run(
        f"sudo sh -c 'cat {workdir}/logs/events-*.jsonl 2>/dev/null'",
        check=False,
    )
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


# The repo-wide pytest-timeout default (60s) bounds wedged unit tests; this
# real-hardware run needs the per-property wait ceiling plus the one-time pi
# install + unit setup + teardown, so override it for this item.
@pytest.mark.timeout(_WAIT_TIMEOUT_S + 600)
def test_agent_runner_should_terminate_before_host_pressure_peaks_on_real_cgroup(
    pi_pre_oom_unit: dict,
    pi_workdir: str,
    pi_venv_python: str,
) -> None:
    # (pi_growth_script is installed transitively via pi_pre_oom_config; the
    # reap check matches its basename directly, so no direct param is needed.)
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

    kinds_seen = sorted({e.get("event") or "" for e in seen_events})
    print(f"[pre-oom] {len(seen_events)} events captured; kinds={kinds_seen}")

    if fired is None:
        # Self-diagnose a zero/weak-signal failure inline (defer symptom via
        # swap.max, child growth via current, leaf OOM, host-pressure envelope)
        # so the failure mode is legible without a separate post-mortem.
        diag = _ssh(
            f"sudo sh -c 'echo swap.max=$(cat {cgroup_path}/memory.swap.max 2>/dev/null); "
            f"echo current=$(cat {cgroup_path}/memory.current 2>/dev/null); "
            f"cat {cgroup_path}/memory.events 2>/dev/null'",
            check=False,
        )
        print(f"[pre-oom][diag] leaf cgroup:\n{diag.stdout}{diag.stderr}")
        psi = [
            s["psi_full_avg10"] for _, s in pressure_samples if s.get("psi_full_avg10") is not None
        ]
        sout = [s["swap_sout"] for _, s in pressure_samples if s.get("swap_sout") is not None]
        print(
            f"[pre-oom][diag] {len(pressure_samples)} host samples; "
            f"psi_full_avg10 {(min(psi), max(psi)) if psi else 'NA'}; "
            f"swap_sout {(min(sout), max(sout)) if sout else 'NA'}"
        )

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
        "before shipping this release; (b) serve itself is swap-starved in the "
        "150M co-resident leaf and never got to sample -- bump MemoryMax so "
        "the growth child dominates without starving the supervisor, then "
        "re-run. Check host_cgroup_memory_limit.defer first: True "
        "means the fixture's cgroup shape is wrong (MemorySwapMax not really "
        "unbounded), not (a) or (b)."
    )
    assert outcome is not None

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
        # fired - peak: positive means agent-runner acted AFTER PSI-full crested
        # (the peak preceded the terminate); negative means pressure was still
        # rising when it acted (peak came after).
        margin_s = (fired_dt - peak_dt).total_seconds()
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
        # sudo: the growth child runs as root (child of the root serve unit),
        # so a bare pgrep could miss it if the host hides other users' PIDs.
        # The `[g]` bracket makes the pattern match the child's cmdline
        # ("growth_child.py") but NOT the sudo/pgrep wrapper's own cmdline
        # (literal "[g]rowth_child.py"), which would otherwise self-match and
        # make the check report "still running" forever.
        reaped = _wait_until(
            lambda: _ssh("sudo pgrep -f '[g]rowth_child.py'", check=False).returncode != 0,
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
    oom_kill = _parse_oom_kill(leaf_events.stdout)
    assert oom_kill == 0, (
        f"leaf memory.events oom_kill={oom_kill} at {cgroup_path} -- the "
        "cgroup's own OOM killer fired, meaning the child was kernel-OOM-"
        "killed instead of swapping into the (supposedly unbounded) host "
        "swap. Check MemorySwapMax is really 'infinity' on the live unit "
        f"({unit}), not just in the unit file this fixture wrote."
    )


def _parse_oom_kill(text: str) -> int:
    oom_kill = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "oom_kill":
            oom_kill = int(parts[1])
    return oom_kill


def _max_oom_kill_delta(events: list[dict]) -> int:
    deltas = [
        int(e.get("events_oom_kill_delta") or 0)
        for e in events
        if e.get("event") == "round_cgroup_memory"
    ]
    return max(deltas, default=0)


def _assert_control(
    events: list[dict],
    cgroup_path: str,
    host_ok: bool,
    *,
    leaf_oom_kill: int,
    child_gone: bool,
    serve_active: bool,
) -> None:
    kinds = {e.get("event") for e in events}

    limit = [e for e in events if e.get("event") == "host_cgroup_memory_limit"]
    assert limit, (
        f"no host_cgroup_memory_limit event at {cgroup_path}; kinds={sorted(str(k) for k in kinds)}"
    )
    assert limit[0].get("defer") is True, (
        f"host_cgroup_memory_limit.defer is {limit[0].get('defer')!r}, expected True "
        f"(control unit must be MemorySwapMax=0). event={limit[0]}"
    )

    assert "round_mem_terminated" not in kinds, (
        "control arm must not cooperatively terminate: round_mem_terminated present"
    )
    assert "memory_high_engaged" not in kinds, (
        "control arm must not engage the soft brake: memory_high_engaged present"
    )

    oom_delta = _max_oom_kill_delta(events)
    if oom_delta < 1:
        if leaf_oom_kill >= 1:
            pytest.fail(
                "INVALID: leaf memory.events oom_kill "
                f">= 1 at {cgroup_path} but round_cgroup_memory.events_oom_kill_delta "
                "was never written (serve likely died before emit)"
            )
        pytest.fail(
            "control arm expected events_oom_kill_delta >= 1 (or INVALID); "
            f"got delta={oom_delta}, leaf oom_kill={leaf_oom_kill}, "
            f"kinds={sorted(str(k) for k in kinds)}"
        )

    if "mem_pressure_deferred_to_cgroup" in kinds:
        path_name = "DEFER-FIRST"
    else:
        crit = [e for e in events if e.get("event") == "round_mem_critical_sample"]
        max_consec = max(
            (int(e.get("consecutive") or 0) for e in crit),
            default=0,
        )
        if max_consec < _CRITICAL_CONSECUTIVE_SAMPLES:
            path_name = "KERNEL-FIRST"
        else:
            pytest.fail(
                "control arm could not name DEFER-FIRST or KERNEL-FIRST: "
                "no mem_pressure_deferred_to_cgroup and "
                f"max round_mem_critical_sample.consecutive={max_consec} "
                f"(not < {_CRITICAL_CONSECUTIVE_SAMPLES})"
            )
    print(f"CONTROL-PATH: {path_name}")

    if not child_gone and not serve_active:
        pytest.fail(
            "INVALID: serve/round died and the growth child did not "
            "(kernel likely picked serve; memory.oom.group is unset on purpose)"
        )
    assert child_gone, (
        "growth child still running after the control wait -- kernel cgroup-OOM "
        "did not contain the capped child"
    )

    assert host_ok, (
        "host did not answer a follow-up true after the control arm -- "
        "possible host-wide unresponsiveness"
    )


def _run_control_arm(*, run, unit_info: dict, workdir: str) -> None:
    unit = unit_info["unit"]
    cgroup_path = unit_info["cgroup_path"]
    events: list[dict] = []
    deadline = time.monotonic() + _CONTROL_WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        events = _read_events_via(run, workdir)
        child_gone = run("sudo pgrep -f '[g]rowth_child.py'", check=False).returncode != 0
        has_round_mem = any(e.get("event") == "round_cgroup_memory" for e in events)
        if child_gone and has_round_mem:
            break
        time.sleep(_POLL_INTERVAL_S)

    kinds = sorted({e.get("event") or "" for e in events})
    print(f"[control] {len(events)} events captured; kinds={kinds}")

    child_gone = run("sudo pgrep -f '[g]rowth_child.py'", check=False).returncode != 0
    if _max_oom_kill_delta(events) < 1 or not child_gone:
        diag = run(
            f"sudo sh -c 'echo current=$(cat {cgroup_path}/memory.current 2>/dev/null); "
            f"echo swap.current=$(cat {cgroup_path}/memory.swap.current 2>/dev/null); "
            f"cat {cgroup_path}/memory.events 2>/dev/null'",
            check=False,
        )
        print(f"[control][diag] leaf cgroup:\n{diag.stdout}{diag.stderr}")

    leaf_oom_kill = _parse_oom_kill(
        run(f"sudo cat {cgroup_path}/memory.events", check=False).stdout
    )
    serve_active = run(f"sudo systemctl is-active {unit}", check=False).stdout.strip() == "active"
    host = run("true", check=False)
    _assert_control(
        events,
        cgroup_path,
        host.returncode == 0,
        leaf_oom_kill=leaf_oom_kill,
        child_gone=child_gone,
        serve_active=serve_active,
    )


@pytest.mark.timeout(_CONTROL_WAIT_TIMEOUT_S + 600)
def test_agent_runner_should_let_cgroup_oom_kill_the_capped_child_when_swap_is_bound(
    pi_pre_oom_control_unit: dict,
    pi_workdir: str,
) -> None:
    _run_control_arm(run=_ssh, unit_info=pi_pre_oom_control_unit, workdir=pi_workdir)


@pytest.mark.timeout(_CONTROL_WAIT_TIMEOUT_S + 120)
def test_agent_runner_should_let_cgroup_oom_kill_the_capped_child_when_swap_is_bound_locally(
    cgroup_pre_oom_control_unit: dict,
    cgroup_workdir: str,
) -> None:
    _run_control_arm(run=_local_sh, unit_info=cgroup_pre_oom_control_unit, workdir=cgroup_workdir)
