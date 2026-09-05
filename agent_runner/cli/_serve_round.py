"""Round-lifecycle helpers for the serve loop: spawn/terminate the round
subprocess, pre-round + mid-round memory-pressure gating, and the post-round
give-up decision (config_broken / mem_loop / crash_loop -> exit code).

Split out of ``serve_cmd.py`` (0.2.16 Task 5a) purely to buy LOC headroom
under the module-size gate (``test_module_sizes.py``) and ``cmd()``'s
loop-size gate (``test_layer_2_loop_size.py``) -- no behavior changed.
``serve_cmd.py`` re-imports every name here back into its own namespace, so
``monkeypatch.setattr("agent_runner.cli.serve_cmd.X", ...)`` and a bare call
to ``X(...)`` from ``cmd()`` both keep working exactly as before the split.
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: TID251
from pathlib import Path
from typing import Literal

from agent_runner import host_health, metrics
from agent_runner._serve_policy import (
    _MEM_LOOP_PERSIST_THRESHOLD,
    _MEM_LOOP_PERSIST_WINDOW_S,
    _NO_PROGRESS_SHORT_S,
    CRASH_LOOP_EXIT,
    MEM_LOOP_EXIT,
    MEM_LOOP_PERSISTENT_EXIT,
    PERMANENT_CONFIG_EXIT,
    _mem_loop_decision,
    _no_progress_decision,
    post_round_decision,
)
from agent_runner._throttle import (
    mem_loop_events_in_window,
    pending_recovered,
    round_had_no_progress,
)
from agent_runner.api import (
    emit_config_broken,
    emit_crash_loop,
    emit_host_cgroup_memory_limit,
    emit_mem_loop,
    emit_mem_loop_persistent,
    emit_mem_pressure_deferred_to_cgroup,
    emit_round_deferred,
    emit_round_mem_critical_sample,
    emit_round_mem_terminated,
    emit_round_resumed,
    emit_round_supervisor_wedged,
    emit_stalled_no_progress,
    emit_transient_error_recovered,
)
from agent_runner.clock import SYSTEM_CLOCK, Clock

# Serve-loop-local memory-pressure state for the PRE-ROUND gate: the previous
# sample, so the swap-rate delta tier is evaluable across successive
# _select_and_gate calls, not just within one _spawn_round's own mid-round
# loop. _select_and_gate has no call-to-call state of its own (cmd()
# constructs its arguments fresh every iteration), and cmd()'s loop body
# cannot grow to thread one through (it is at its 140-line budget) — so this
# dict-of-dicts is a stable default, exactly like SYSTEM_CLOCK: cmd() never
# passes anything for it explicitly, so production gets one entry for the
# whole serve process lifetime at zero extra loop LOC.
#
# Keyed by log_dir rather than a single flat dict: a real `agent-runner serve`
# process only ever has ONE log_dir for its lifetime, so this is operationally
# identical to a flat per-process dict in production. The keying exists so
# many _select_and_gate calls across many different tests, in the SAME pytest
# process, stay hermetic from each other automatically (every test already
# uses its own unique tmp_path as log_dir) without any call site having to
# remember to pass an explicit override.
_PRE_ROUND_MEM_STATE_BY_LOG_DIR: dict[Path, dict] = {}


def _memory_pressure_now(cfg, log_dir, sample_fn) -> host_health.Pressure | None:
    """Pre-round host_health read, using (and updating) this ``log_dir``'s
    persisted previous sample so the swap-rate delta tier is evaluable, not
    just PSI/combined-low. The very first call for a given ``log_dir`` has no
    baseline yet (``prev={}``) and so cannot see a swap-rate signal until the
    NEXT call — an honest limitation (there is nothing to diff against), not a
    bug. The mid-round hot loop in :func:`_spawn_round` keeps its own separate
    ``prev_sample`` across its own ticks."""
    state = _PRE_ROUND_MEM_STATE_BY_LOG_DIR.setdefault(log_dir, {})
    cur = sample_fn()
    pressure = host_health.memory_pressure(cur, state.get("prev", {}), cfg.monitor.host_health)
    state["prev"] = cur
    return pressure


def _pressure_is_critical(pressure: host_health.Pressure | None) -> bool:
    return pressure is not None and pressure.severity == "critical"


def _pause_poll(stop, stop_file, runnable_fn, sleep_fn, chunk_s) -> bool:
    """Chunked (<= chunk_s) sleep until ``runnable_fn()`` is True; break on
    ``stop["requested"]`` or ``stop_file``. Returns True iff a window opened (a
    stop / stop_file break returns False — the pause was interrupted, not resumed).
    Shared by both pause entry points; only the runnable predicate + sleep differ."""
    while not stop["requested"]:
        if stop_file is not None and stop_file.exists():
            return False
        if runnable_fn():
            return True
        sleep_fn(chunk_s)
    return False


def _maybe_pause_for_memory_pressure(
    cfg,
    log_dir,
    stop,
    *,
    sample_fn=metrics.sample,
    clock: Clock = SYSTEM_CLOCK,
    chunk_s: int = 30,
) -> bool:
    """Pre-round admission gate (Group 3 action half): defer the next round
    while host_health reports CRITICAL pressure (0.2.16: narrowed from ANY --
    a mere warning, e.g. swap churn, is the north star's fine). Only HALF the
    coma-preventer — a single round that balloons mid-flight still needs
    :func:`_spawn_round`'s mid-round hard floor to stop it before unresponsiveness.

    Polls exactly like ``serve_cmd._maybe_pause_for_schedule`` via the shared
    :func:`_pause_poll` (this module owns it; ``serve_cmd`` re-imports it back
    for its own schedule-pause use). Emits a paired
    ``round_deferred``/``round_resumed`` (like ``schedule_paused``/
    ``schedule_resumed``) so a long defer does not trip
    ``detect_supervisor_stale`` (see its suppression set in
    ``_monitor_detectors.py``)."""
    pressure = _memory_pressure_now(cfg, log_dir, sample_fn)
    if not _pressure_is_critical(pressure):
        return False
    started = clock.monotonic()
    emit_round_deferred(
        log_dir, severity=pressure.severity, signal=pressure.signal, message=pressure.message
    )
    if _pause_poll(
        stop,
        cfg.runtime.stop_file,
        lambda: not _pressure_is_critical(_memory_pressure_now(cfg, log_dir, sample_fn)),
        clock.sleep,
        chunk_s,
    ):
        emit_round_resumed(log_dir, deferred_for_s=int(clock.monotonic() - started))
    return True


def _maybe_emit_recovered(log_dir, active=None) -> None:
    """Emit ``transient_error_recovered`` for every agent whose throttle cleared via
    skip-around (rotation, no back-off sleep) and so left no breadcrumb. Per-agent, so
    one agent recovering while a sibling is still throttled still gets its breadcrumb.
    Events-derived + dedup-safe (see ``pending_recovered``): the back-off path already
    emits its own recovered, so this stays quiet there and never double-emits.

    ``active`` is the current ``_active_throttles`` map when the caller already has
    it (the skip loop does), passed through to spare ``pending_recovered`` a rescan."""
    for agent, classification, throttled_for_s in pending_recovered(log_dir, active=active):
        emit_transient_error_recovered(
            log_dir, classification=classification, agent=agent, throttled_for_s=throttled_for_s
        )


# Grace after TERMing a wedged round before killpg: the round's own SIGTERM handler
# reaps its agent pgroup (agent_runtime.REAP_GRACE_S) then exits, so allow that plus
# margin. test_spawn_round_wedged asserts it stays >= REAP_GRACE_S so the two never
# drift.
_ROUND_TERM_GRACE_S = 15


def _terminate_round(proc: subprocess.Popen) -> int:
    """TERM the round leader first (fires its SIGTERM handler → agent pgroup reaped +
    flock/sidecar released), grace, then killpg as last resort. Returns the returncode.

    Reads the grace period off this module's own ``_ROUND_TERM_GRACE_S`` global (no
    more reach-back through ``serve_cmd`` -- that module never used this constant
    itself, only re-exported it), so ``monkeypatch.setattr(_serve_round,
    "_ROUND_TERM_GRACE_S", ...)`` (test_spawn_round_wedged.py) lands directly."""
    proc.terminate()
    try:
        return proc.wait(timeout=_ROUND_TERM_GRACE_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        return proc.wait(timeout=10)


# Mid-round hard floor: how often (in clock.monotonic() seconds, not per-tick)
# _spawn_round resamples host_health while a round is in flight. Coarser than
# the 1s proc.wait tick so a healthy round pays no real sampling cost.
_MEM_CHECK_INTERVAL_S = 10


def _mid_round_action(
    cfg, defer_to_cgroup: bool, critical_streak: int
) -> Literal["terminate", "defer", "count_only"]:
    """Pure, subprocess-free verdict (0.2.17 Task 2) for a critical mid-round
    tick, once the caller has already incremented ``critical_streak`` and
    (when the streak is within the emit cap) emitted
    ``round_mem_critical_sample`` for it. ``cfg`` is the round's
    ``host_health_cfg``.

    - ``"count_only"`` — either the off switch (``cfg.in_round_mem_terminate``
      False) or the streak hasn't yet reached
      ``cfg.mem_critical_consecutive_samples``: keep sampling, no action.
      **The off switch wins even when ``defer_to_cgroup`` is True** — it means
      no action at all, not "defer instead of terminate", matching the
      pre-extraction nesting where the defer branch sat INSIDE the
      ``in_round_mem_terminate`` guard and so was never reachable when it was
      False (no ``mem_pressure_deferred_to_cgroup`` emit either).
    - ``"defer"`` — sustained critical pressure, but the cgroup's own
      (mem+swap) budget is bounded end to end, so kernel cgroup-OOM will
      contain the agent; the caller emits ``mem_pressure_deferred_to_cgroup``
      (once per episode) instead of terminating.
    - ``"terminate"`` — sustained critical pressure, no cgroup containment to
      defer to: the caller ``_terminate_round``s and emits
      ``round_mem_terminated``.
    """
    if not (cfg.in_round_mem_terminate and critical_streak >= cfg.mem_critical_consecutive_samples):
        return "count_only"
    return "defer" if defer_to_cgroup else "terminate"


def _spawn_round(
    round_argv: list[str],
    round_log_path: Path,
    round_env: dict,
    *,
    timeout_s: int,
    round_num: int,
    host_health_cfg=None,
    defer_to_cgroup: bool = False,
    clock: Clock = SYSTEM_CLOCK,
    sample_fn=metrics.sample,
) -> int:
    """Spawn `agent-runner round` in its OWN process group under an outer wall-clock
    ceiling. Breaks on the timeout DEADLINE only: on breach, emit
    round_supervisor_wedged and escalate TERM → grace → killpg (TERM-first is
    load-bearing: a bare killpg SIGKILLs before the round can reap its agent).
    Graceful serve stop is NOT handled here — the serve loop's existing top-of-loop
    `while not stop["requested"]` check + post-round break let the current round
    finish (the documented stop contract: runbook.md, 0.2.11 CHANGELOG). Returns
    the round returncode.

    ``host_health_cfg`` (None by default — existing callers get byte-identical
    behavior) arms the mid-round hard floor: every ~``_MEM_CHECK_INTERVAL_S``
    seconds this resamples host_health. On CRITICAL pressure it increments a
    ``critical_streak`` counter (reset to 0 on any non-critical verdict) and,
    once the streak reaches ``host_health_cfg.mem_critical_consecutive_samples``
    (3 by default), ``_terminate_round``s the round and emits
    ``round_mem_terminated`` — the actual coma-preventer for a single round
    that balloons mid-flight (the pre-round gate in ``_select_and_gate`` only
    samples at round boundaries). Requiring SUSTAINED critical pressure (not
    a single sample) matches the north star: this floor prevents
    unresponsiveness, not swapping, so a transient spike must not kill a
    round. ``host_health_cfg.in_round_mem_terminate`` is the off switch: when
    False the streak still counts (an operator may still want the signal
    surfaced) but ``_terminate_round`` is never called.

    ``defer_to_cgroup`` (0.2.16 Task 3 -- from ``metrics.cgroup_memory_limits``;
    True only when memory.max AND memory.swap.max are BOTH finite) OVERRIDES
    ``in_round_mem_terminate=True`` at the same crossing: cgroup-OOM will
    contain the agent on its own, so the host-wide kill emits
    ``mem_pressure_deferred_to_cgroup`` once per episode instead. The streak
    still counts either way -- only terminate-vs-defer changes.

    Each tick's swap-out delta is measured against the PREVIOUS TICK's
    sample (``prev_tick_sample``), not the round-start sample -- a
    per-interval rate. Pinning round-start as ``prev`` (0.2.15's choice)
    made the delta monotone (never fell back once crossed), so the streak
    could never reset for the swap leg and a long round died on it
    regardless of host recovery. PSI-full is an independent immediate
    critical path (current sample alone, no baseline needed).

    0.2.16: every critical tick also emits ``round_mem_critical_sample`` for
    near-miss calibration (0.2.17: capped -- see below); ``round_mem_terminated``
    now carries the streak + ``Pressure.context`` too. The cap:
    ``2 * host_health_cfg.mem_critical_consecutive_samples`` consecutive
    ticks (1..6 at the default 3) -- a sustained-critical don't-terminate
    run (cgroup-defer, or the off switch) would otherwise write one event
    per ~10s tick for up to a whole ``round_timeout_s``. The streak still
    resets to 0 on any non-critical tick, so the cap is per streak-episode:
    sampling resumes from 1 the next time critical pressure recurs."""
    log_dir = round_log_path.parent
    with round_log_path.open("w") as f:
        proc = subprocess.Popen(
            round_argv,
            env=round_env,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = clock.monotonic() + timeout_s
            next_mem_check = (
                clock.monotonic() + _MEM_CHECK_INTERVAL_S if host_health_cfg is not None else None
            )
            # prev_tick_sample trails one tick behind cur_sample (reassigned
            # after every check below), so the swap-out delta
            # host_health.memory_pressure computes is a PER-INTERVAL rate —
            # see the docstring above for why this replaced the 0.2.15
            # round-start baseline. critical_streak is the hysteresis
            # counter: only sustained (not single-sample) critical pressure
            # terminates the round.
            prev_tick_sample: dict | None = None
            critical_streak = 0
            cgroup_defer_notified = False
            while True:
                try:
                    return proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                if clock.monotonic() >= deadline:
                    break
                if next_mem_check is not None and clock.monotonic() >= next_mem_check:
                    cur_sample = sample_fn()
                    if prev_tick_sample is None:
                        prev_tick_sample = cur_sample
                    pressure = host_health.memory_pressure(
                        cur_sample, prev_tick_sample, host_health_cfg
                    )
                    prev_tick_sample = cur_sample
                    if pressure is not None and pressure.severity == "critical":
                        critical_streak += 1
                        if critical_streak <= 2 * host_health_cfg.mem_critical_consecutive_samples:
                            emit_round_mem_critical_sample(
                                log_dir,
                                round_num=round_num,
                                consecutive=critical_streak,
                                context=pressure.context,
                            )
                        action = _mid_round_action(
                            host_health_cfg, defer_to_cgroup, critical_streak
                        )
                        if action == "terminate":
                            returncode = _terminate_round(proc)
                            emit_round_mem_terminated(
                                log_dir,
                                pid=proc.pid,
                                severity=pressure.severity,
                                signal=pressure.signal,
                                message=pressure.message,
                                consecutive=critical_streak,
                                context=pressure.context,
                            )
                            return returncode
                        if action == "defer" and not cgroup_defer_notified:
                            emit_mem_pressure_deferred_to_cgroup(
                                log_dir,
                                pid=proc.pid,
                                signal=pressure.signal,
                                message=pressure.message,
                            )
                            cgroup_defer_notified = True
                    else:
                        critical_streak = 0
                        cgroup_defer_notified = False
                    next_mem_check = clock.monotonic() + _MEM_CHECK_INTERVAL_S
        except BaseException:
            _terminate_round(proc)  # exception-path cleanup: never orphan the round pgroup
            raise
        # Safety kill BEFORE the observability write: the wedged round must not
        # keep burning wall-clock (and, if its own reap hangs, its agent's
        # budget) waiting on the least-reliable step (a disk write) to finish
        # first. emit_round_supervisor_wedged reads proc.pid, which stays a
        # valid attribute after the process has already been terminated.
        returncode = _terminate_round(proc)
        emit_round_supervisor_wedged(
            log_dir, pid=proc.pid, timeout_s=timeout_s, log_path=round_log_path
        )
        return returncode


def _probe_and_emit_cgroup_defer(log_dir: Path) -> bool:
    """Probe this process's cgroup v2 memory budget once at serve startup,
    emit host_cgroup_memory_limit for observability, and return whether the
    mid-round hard floor should defer to kernel cgroup-OOM: True only when
    BOTH memory.max and memory.swap.max are finite (the field host's
    MemoryMax=320M + MemorySwapMax=160M shape) -- that budget is bounded end
    to end, so cgroup-OOM WILL fire and contain the agent while the host
    stays responsive, making our cruder host-wide floor redundant at best.
    Only-memory.max-finite (systemd's MemoryMax-without-MemorySwapMax
    default) leaves swap unbounded -- the agent just swaps and cgroup-OOM
    never fires, so the floor must stay armed.

    A THIRD plausibility guard on top of "both finite" (0.2.16 fix-wave
    IMPORTANT #1): ``memory_max`` must also be strictly less than the HOST's
    own total RAM. A misconfigured/copy-pasted unit (e.g. ``MemoryMax=1G`` on
    a 462MB host) reports a finite-but-implausible limit that can never
    actually bind -- the process will exhaust host memory long before the
    cgroup's own ceiling, so cgroup-OOM never fires and deferring here would
    leave NOTHING armed to prevent coma. Only a limit tighter than the host
    itself can plausibly trigger before host-wide exhaustion."""
    limits = metrics.cgroup_memory_limits()
    emit_host_cgroup_memory_limit(
        log_dir,
        memory_max=limits["memory_max"],
        memory_swap_max=limits["memory_swap_max"],
        cgroup_path=limits["cgroup_path"],
    )
    return (
        limits["memory_max"] is not None
        and limits["memory_swap_max"] is not None
        and limits["memory_max"] < metrics.mem_total_bytes()
    )


def post_round_verdicts(
    cfg,
    *,
    log_dir,
    round_log_path,
    r_returncode: int,
    round_duration_s: float,
    round_throttle_active: bool,
    outcome,
    consecutive_crashes: int,
    consecutive_mem_terminations: int,
    consecutive_no_progress: int,
    clock: Clock = SYSTEM_CLOCK,
) -> tuple[int | None, int, tuple[int, int, int]]:
    """One round's full give-up orchestration (0.2.17 Task 2 — renamed from
    ``round_outcome_exit_code`` and widened to run the three independent
    breakers itself, absorbing the sequence that used to live inline in
    ``serve_cmd.cmd()``'s post-round block): ``post_round_decision``'s
    crash-loop, ``_mem_loop_decision``, and ``_no_progress_decision``, off
    this round's already-scanned ``round_throttle_active``/``outcome`` (see
    ``serve_cmd._round_scan`` — INVARIANT 3, one events-tail scan per round;
    ``outcome.mem_terminated`` feeds ``_mem_loop_decision`` directly), then
    resolves them to a single verdict.

    Returns ``(exit_code_or_None, delay_s, streaks)``: ``exit_code_or_None``
    is ``None`` to keep looping, or the exit code ``cmd()`` should break the
    loop on; ``delay_s`` is ``post_round_decision``'s restart delay (the
    caller breaks before ever sleeping on it when a give-up fires); ``streaks``
    is the updated ``(consecutive_crashes, consecutive_mem_terminations,
    consecutive_no_progress)`` triple the caller must carry into the next
    iteration regardless of which verdict (if any) fired.

    Order is significant: config_broken > mem_loop(_persistent) > crash_loop >
    stalled_no_progress (a mem-terminated round always has
    ``throttle_active=True``, so ``post_round_decision`` never returns
    ``crash_loop`` for it anyway — this ordering just makes the precedence
    explicit; crash_loop and stalled_no_progress are mutually exclusive by
    construction — one keys on ``returncode != 0``, the other on ``== 0``).

    A ``mem_loop`` verdict escalates further (0.2.16 Task 5 — cross-restart
    convergence): ``MEM_LOOP_EXIT`` (71) alone resets on every serve process
    restart, so a host stuck in sustained pressure respawns into the
    identical break-then-restart loop forever. Counting prior ``mem_loop``
    episodes in the events tail within ``_MEM_LOOP_PERSIST_WINDOW_S`` (events-
    derived, no state file — see :func:`agent_runner._throttle.
    mem_loop_events_in_window`) turns this run's mem_loop into the
    ``_MEM_LOOP_PERSIST_THRESHOLD``-th in-window occurrence, at which point
    serve STOPS for real (``MEM_LOOP_PERSISTENT_EXIT``, a deliberate give-up
    like config_broken/crash_loop) instead of the usual restartable 71.

    A ``stalled_no_progress`` verdict (0.2.16 Task 6 — the exit-0 no-progress
    breaker: some CLIs, e.g. pi, exit 0 on a provider failure that never
    reaches the model) deliberately returns ``CRASH_LOOP_EXIT`` — the SAME
    give-up code as crash_loop, not a new one — since it is the identical
    verdict ("an unknown failure kept recurring, stop for real") reached via a
    different signal."""
    action, delay, consecutive_crashes = post_round_decision(
        returncode=r_returncode,
        duration_s=round_duration_s,
        throttle_active=round_throttle_active,
        consecutive=consecutive_crashes,
        restart_delay_s=cfg.runtime.restart_delay_s,
    )
    mem_action, consecutive_mem_terminations = _mem_loop_decision(
        mem_terminated=outcome.mem_terminated, consecutive=consecutive_mem_terminations
    )
    # Exit-0 no-progress breaker (0.2.16 Task 6): pi-class CLIs exit 0 on a
    # provider failure that never reached the model, invisible to the crash
    # loop above (that one keys on a non-zero exit).
    no_progress = round_had_no_progress(
        log_dir,
        returncode=r_returncode,
        duration_s=round_duration_s,
        threshold_s=_NO_PROGRESS_SHORT_S,
        throttle_active=round_throttle_active,
        outcome=outcome,
    )
    noprogress_action, consecutive_no_progress = _no_progress_decision(
        no_progress=no_progress, consecutive=consecutive_no_progress
    )
    streaks = (consecutive_crashes, consecutive_mem_terminations, consecutive_no_progress)
    if action == "config_broken":
        # classify_round_exit maps ANY ConfigError to this exit code (Group
        # A) — not only a startup-battery check failure (e.g. _phase_for's
        # stale-serve-cache ConfigError takes this same path with the
        # battery never having run this round), so the reason names the
        # verdict, not an assumed cause.
        emit_config_broken(
            log_dir, reason=f"permanent config failure (round exited {r_returncode})"
        )
        return PERMANENT_CONFIG_EXIT, delay, streaks
    if mem_action == "mem_loop":
        occurrence = mem_loop_events_in_window(log_dir, clock, _MEM_LOOP_PERSIST_WINDOW_S) + 1
        if occurrence >= _MEM_LOOP_PERSIST_THRESHOLD:
            emit_mem_loop_persistent(
                log_dir,
                consecutive=occurrence,
                exit_code=r_returncode,
                log_path=round_log_path,
            )
            return MEM_LOOP_PERSISTENT_EXIT, delay, streaks
        emit_mem_loop(
            log_dir,
            consecutive=consecutive_mem_terminations,
            exit_code=r_returncode,
            log_path=round_log_path,
        )
        return MEM_LOOP_EXIT, delay, streaks
    if action == "crash_loop":
        emit_crash_loop(
            log_dir,
            consecutive=consecutive_crashes,
            exit_code=r_returncode,
            log_path=round_log_path,
        )
        return CRASH_LOOP_EXIT, delay, streaks
    if noprogress_action == "stalled_no_progress":
        emit_stalled_no_progress(
            log_dir,
            consecutive=consecutive_no_progress,
            exit_code=r_returncode,
            log_path=round_log_path,
        )
        return CRASH_LOOP_EXIT, delay, streaks
    return None, delay, streaks
