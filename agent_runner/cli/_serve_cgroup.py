"""Cgroup-pressure spine for the serve loop: per-round cgroup memory tracking
(peak memory.current/swap.current, memory.events deltas), the
round_cgroup_memory and round_oom_killed emits, and the startup probe that
decides whether the mid-round hard floor (``_serve_round._spawn_round``)
should defer to kernel cgroup-OOM instead of terminating.

Split out of ``_serve_round.py`` purely to buy LOC headroom under the
module-size gate (``test_module_sizes.py``) -- no behavior changed.
``_serve_round`` imports ``_emit_round_cgroup_memory``,
``_maybe_emit_oom_killed``, and ``_stash_round_cgroup_state`` back into its
own namespace for ``_spawn_round``/``post_round_verdicts`` to call;
``serve_cmd.py`` imports ``_probe_and_emit_cgroup_defer`` directly. Import
direction is one-way: this module imports nothing from ``agent_runner.cli``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_runner import metrics
from agent_runner._serve_policy import _ROUND_UNREAPED_RC
from agent_runner.api import (
    emit_host_cgroup_memory_limit,
    emit_memory_high_released,
    emit_memory_high_write_failed,
    emit_round_cgroup_memory,
    emit_round_oom_killed,
)

# Per-round cgroup pressure: baseline (memory.events at spawn start) + running
# peak (memory.current/memory.swap.current over the round's existing mid-round
# ticks), written by _stash_round_cgroup_state (called from
# _serve_round._spawn_round's closure) and read once by
# _emit_round_cgroup_memory for the round_cgroup_memory delta. Same
# per-log_dir stable-default pattern as
# _serve_round._PRE_ROUND_MEM_STATE_BY_LOG_DIR (one entry per serve process in
# production) -- popped on read so a stale entry can never leak into the next
# round.
_ROUND_CGROUP_STATE_BY_LOG_DIR: dict[Path, dict] = {}

# Boot-armed soft-brake gate: True iff the brake is configured on AND this
# process's own cgroup leaf is delegated (memory.high writable) -- probed ONCE
# at serve boot (_probe_and_emit_cgroup_defer already reads cgroup_delegated),
# read every mid-round tick by _serve_round via _brake_step_for. Same
# per-log_dir stable-default pattern as _ROUND_CGROUP_STATE_BY_LOG_DIR (one
# entry per serve process in production); cmd() cannot grow to thread it (145
# budget), so it rides in a module dict, not an argument.
_BRAKE_ARMED_BY_LOG_DIR: dict[Path, bool] = {}


def _brake_step_for(log_dir: Path, host_health_cfg) -> int | None:
    """The armed soft-brake step_pct for this log_dir, or None when the brake is
    disarmed: config off, or the boot probe found the leaf undelegated / no
    cgroup v2. The step itself is single-sourced from config; the dict only
    carries the boot-time delegated-and-on decision."""
    if not host_health_cfg.brake.memory_high:
        return None
    if not _BRAKE_ARMED_BY_LOG_DIR.get(log_dir, False):
        return None
    return host_health_cfg.brake.memory_high_step_pct


def _stash_round_cgroup_state(
    log_dir: Path, cg_base: dict, peak_current: int, peak_swap: int
) -> None:
    """Write this round's cgroup baseline+peak -- called from
    ``_serve_round._spawn_round``'s ``_stash_cgroup`` closure at every exit
    path (clean return, mid-round terminate, wedged-timeout kill), so the
    write and the :func:`_emit_round_cgroup_memory` read both live behind
    this module's own door. A no-op when ``cg_base`` is falsy: this host has
    no finite cgroup bound, so nothing is stashed and
    :func:`_emit_round_cgroup_memory` correctly no-ops for the round."""
    if cg_base:
        _ROUND_CGROUP_STATE_BY_LOG_DIR[log_dir] = {
            "baseline_events": cg_base.get("memory_events", {}),
            "peak_current": peak_current,
            "peak_swap": peak_swap,
            "bounding_cgroup_path": cg_base.get("cgroup_path"),
        }


def _emit_round_cgroup_memory(
    log_dir: Path, round_log_path: Path, round_num: int
) -> tuple[dict, int | None]:
    """Emit ``round_cgroup_memory`` from the per-round baseline/peak
    :func:`_spawn_round` stashed, diffing ``memory.events`` at the bounding
    ancestor. Returns ``(cur, oom_kill_baseline)``: ``cur`` is the current
    usage dict (a future round-outcome classifier can reuse it as the one
    classification-time cgroup read); ``oom_kill_baseline`` is this round's
    starting ``memory.events.oom_kill`` count, for :func:`_maybe_emit_oom_killed`
    to diff ``cur`` against -- the one place both halves of that single
    cgroup read are available together, so detecting an OOM-kill never needs
    a second sysfs read. Both are ``({}, None)`` (no emit) when this host has
    no finite cgroup bound -- ``_spawn_round`` never stashes state in that
    case -- when the bounding ancestor directory has vanished by round end
    (``cur`` empty: renamed/removed mid-round -- a still-existing directory
    is read at face value even if it became unbounded, since the cached
    ``bounding_cgroup`` read skips the memory.max walk that would notice),
    or when the STARTING ``memory.events`` read failed (``baseline_events``
    empty). The middle case must skip the emit rather than write all-zero
    deltas against a now-stale ``bounding_cgroup_path`` -- that would misreport "no
    pressure" when the truth is "can no longer tell"; the last case must
    skip it rather than diff against zero, which would report the cumulative
    since-cgroup-creation counter as this round's delta."""
    state = _ROUND_CGROUP_STATE_BY_LOG_DIR.pop(log_dir, None)
    if not state:
        return {}, None
    # Reuse the bounding ancestor _spawn_round already resolved at round
    # start (stashed as bounding_cgroup_path) -- skips re-walking the
    # ancestor chain for this round's third (and last) cgroup_memory_usage
    # read; see metrics.cgroup_memory_usage's bounding_cgroup docstring.
    cur = metrics.cgroup_memory_usage(bounding_cgroup=state["bounding_cgroup_path"])
    if not cur:
        return {}, None
    base_ev = state["baseline_events"]
    if not base_ev:
        # The spawn-start memory.events read failed (metrics returns {} on
        # OSError) but the round-end read above succeeded: diffing against an
        # empty baseline would report the cumulative-since-cgroup-creation
        # counter as if it were THIS round's delta -- a bogus absolute is
        # worse than no event at all, so skip the emit entirely (also skips
        # the OOM-kill baseline return below, for the same reason).
        return {}, None
    cur_ev = cur.get("memory_events", {})
    oom_kill_baseline = base_ev.get("oom_kill", 0)

    def _delta(key: str) -> int:
        return max(0, cur_ev.get(key, 0) - base_ev.get(key, 0))

    # One sample() at emit -- corroborating IO/mem-PSI, omitted when unread.
    s = metrics.sample()
    emit_round_cgroup_memory(
        log_dir,
        round_num=round_num,
        memory_current_peak=state["peak_current"],
        memory_swap_current_peak=state["peak_swap"],
        events_high_delta=_delta("high"),
        events_max_delta=_delta("max"),
        events_oom_delta=_delta("oom"),
        events_oom_kill_delta=_delta("oom_kill"),
        bounding_cgroup_path=state["bounding_cgroup_path"],
        io_psi_some_avg10=s.get("io_psi_some_avg10"),
        io_psi_full_avg10=s.get("io_psi_full_avg10"),
        psi_full_total=s.get("psi_full_total"),
    )
    return cur, oom_kill_baseline


def _mark_partial_log(round_log_path: Path) -> bool:
    """Append a supervisor-owned trailer marking a round log as partial (the
    agent was OOM-killed mid-write) -- a residue marker, never round CONTENT,
    so a later reader (0.3: the next round's own prompt) can tell this log's
    tail is supervisor-added, not the agent's own output. Best-effort: returns
    whether the trailer was actually written."""
    try:
        with round_log_path.open("a", encoding="utf-8") as f:
            f.write("\n[agent-runner] round log truncated: cgroup OOM-kill\n")
        return True
    except OSError:
        return False


def _maybe_emit_oom_killed(
    log_dir: Path,
    round_log_path: Path,
    round_num: int,
    cur_usage: dict,
    r_returncode: int,
    oom_kill_baseline: int | None,
) -> None:
    """If the bounding cgroup's ``memory.events.oom_kill`` counter rose over
    this round AND this round itself died to that kill, emit the
    pointer-only ``round_oom_killed``. Uses ``cur_usage`` and
    ``oom_kill_baseline`` -- both halves of the SAME read
    :func:`_emit_round_cgroup_memory` already took, threaded through
    explicitly by the caller: no second sysfs read at classification time.

    The bounding cgroup can be a shared ANCESTOR slice (not this round's own
    cgroup), so a rising counter does not by itself mean this round was the
    victim -- a SIBLING process under the same ancestor can trip it while
    this round exits cleanly. Gated on ``r_returncode`` actually being a
    kill (``_ROUND_UNREAPED_RC`` / 137, or the reaped-SIGKILL form -9): only
    then is a positive delta attributed to this round, for both the emitted
    event and the log's partial-log trailer -- a clean-exit round's INTACT
    log must never be marked truncated on a sibling's OOM.

    Purely additive observability: this function returns nothing consumed by
    ``post_round_verdicts``, and its caller runs it only AFTER that function's
    give-up decisions are already computed, so it cannot influence the
    crash-loop / mem-loop / stalled-no-progress verdict or the round's exit
    code -- a kernel SIGKILL still exits 137 and counts toward the crash streak
    exactly as before this event existed."""
    if not cur_usage or oom_kill_baseline is None:
        return
    delta = max(0, cur_usage.get("memory_events", {}).get("oom_kill", 0) - oom_kill_baseline)
    round_was_killed = r_returncode == _ROUND_UNREAPED_RC or r_returncode == -9
    if delta <= 0 or not round_was_killed:
        return
    try:
        log_bytes = round_log_path.stat().st_size
    except OSError:
        log_bytes = 0
    emit_round_oom_killed(
        log_dir,
        round_num=round_num,
        log_path=round_log_path,
        log_bytes=log_bytes,
        oom_kill_delta=delta,
        partial_log=_mark_partial_log(round_log_path),
    )


# Below this swap-cap-as-percent-of-host-swap threshold, the startup advisory
# fires: the operator has bounded memory.swap.max to well under what the host
# actually has, which is the exact blind spot that let the mid-round floor
# terminate a round the kernel would have contained on a wider cap (field
# report ask #3). Advisory only -- never changes the operator's cgroup/unit.
_SWAP_CAP_ADVISORY_PCT = 25.0

# The delegation-readiness / brake-inert advisory. Fires in two DISTINCT
# cases, both meaning "memory.high can't be written from here":
#   (1) delegated is False (CONFIRMED not delegated -- systemd Delegate=yes
#       is absent) AND memory.max/memory.high are already bound (memory_high
#       set, or the own_scope "add memory.high" hint above just fired) --
#       the existing config is informational-only without delegation.
#   (2) the operator explicitly turned the soft-brake on
#       ([monitor.host_health.brake] memory_high=true, threaded in here as
#       brake_memory_high) but delegated is not True (False OR None) -- the
#       brake will silently do nothing, worth announcing at boot even with
#       no memory.max/memory.high configured yet.
# Deliberately NOT ``delegated is not True`` across the board: cgroup_delegated
# returns None for two different reasons -- cgroup v2 unavailable (harmless)
# AND the leaf's own os.stat() raising OSError (delegation status genuinely
# UNKNOWN, even though memory_high/own_scope's ancestor-walk resolved fine).
# Broadening case (1) to None would assert "not writable" where we actually
# don't know -- an overclaim this release can't afford. Case (2) is the one
# exception: the operator asked for the brake, so ANY non-confirmed-delegated
# state (False or unknown) is worth flagging as "may be inert" up front.
# Advisory only; this probe and its hint never write to the cgroup or the unit.
_UNDELEGATED_HINT = (
    "memory.high on this cgroup is not writable by this process; the soft-brake "
    "is inert. Run serve as a user-mode unit (`systemctl --user` + `loginctl "
    "enable-linger`) or as a root system unit."
)


def brake_report_state(enabled: bool, delegated: bool | None) -> str:
    """The operator-facing soft-brake state for doctor/peek: ``off`` (not
    configured), ``armed`` (on AND the leaf is delegated so memory.high is
    writable), or ``inert(<reason>)`` (on but the write path can't arm)."""
    if not enabled:
        return "off"
    if delegated is True:
        return "armed"
    return "inert(undelegated)" if delegated is False else "inert(no cgroup v2)"


def current_brake_state(cfg) -> str:
    """Resolve the soft-brake state string for `cfg` from live cgroup facts --
    the one-shot ``cgroup_memory_limits() -> cgroup_delegated(self_cgroup=...) ->
    brake_report_state`` sequence, shared by peek (cli.common.emit) and doctor
    so neither re-derives it inline."""
    limits = metrics.cgroup_memory_limits()
    delegated = metrics.cgroup_delegated(self_cgroup=limits["cgroup_path"])
    return brake_report_state(cfg.monitor.host_health.brake.memory_high, delegated)


def _release_brake(log_dir: Path, round_num: int, previous: str, *, reason: str) -> bool:
    """Restore memory.high on serve's own leaf to `previous` and emit the
    matching event: ``memory_high_released`` on success, ``memory_high_write_failed``
    (errno None -- restore only yields a bool) on OSError. Returns whether the
    restore succeeded; callers own their own brake_engaged/brake_previous/
    brake_restore_failed transitions off that result. Shared by _spawn_round's
    mid-round recovery-release site and its unconditional round-end finally."""
    if metrics.restore_leaf_memory_high(previous):
        emit_memory_high_released(log_dir, round_num=round_num, reason=reason)
        return True
    emit_memory_high_write_failed(log_dir, round_num=round_num, errno=None)
    return False


def _probe_and_emit_cgroup_defer(log_dir: Path, *, brake_memory_high: bool = False) -> bool:
    """Probe this process's cgroup v2 memory budget once at serve startup,
    emit host_cgroup_memory_limit (including the ``defer`` decision, not just
    the inputs) for observability, and return whether the mid-round hard
    floor should defer to kernel cgroup-OOM: True only when
    BOTH memory.max and memory.swap.max are finite (the field host's
    MemoryMax=320M + MemorySwapMax=160M shape) -- that budget is bounded end
    to end, so cgroup-OOM WILL fire and contain the agent while the host
    stays responsive, making our cruder host-wide floor redundant at best.
    Only-memory.max-finite (systemd's MemoryMax-without-MemorySwapMax
    default) leaves swap unbounded -- the agent just swaps and cgroup-OOM
    never fires, so the floor must stay armed.

    A THIRD plausibility guard on top of "both finite" (a fix-wave
    IMPORTANT #1): ``memory_max`` must also be strictly less than the HOST's
    own total RAM. A misconfigured/copy-pasted unit (e.g. ``MemoryMax=1G`` on
    a 462MB host) reports a finite-but-implausible limit that can never
    actually bind -- the process will exhaust host memory long before the
    cgroup's own ceiling, so cgroup-OOM never fires and deferring here would
    leave NOTHING armed to prevent coma. Only a limit tighter than the host
    itself can plausibly trigger before host-wide exhaustion.

    A FOURTH plausibility guard, symmetric with the third:
    ``memory_swap_max`` must also be at most the HOST's own total swap
    (``metrics.swap_total_bytes``). A ``MemorySwapMax`` far above host swap
    can't bind before host-wide swap exhaustion either, so it must not
    disarm the floor (an audit-flagged gap). The field host's plausible
    both-finite shape (swap cap at or below host swap) is unaffected and
    still defers.

    This also computes a startup ADVISORY -- carried as fields on this
    SAME host_cgroup_memory_limit event, never a new event kind -- when
    memory.swap.max is bounded but far below the host's own available swap
    (``_SWAP_CAP_ADVISORY_PCT``): the operator capped the cgroup's swap well
    under what the host has, so the mid-round floor may terminate a round
    the kernel would have contained on a wider cap. A second, independent
    advisory recommends ``memory.high`` when ``memory.max`` is set but
    ``memory.high`` isn't -- ONLY when the bounding ancestor is the
    operator's OWN leaf (``bounding_cgroup_path == cgroup_path``): since
    ``memory.max`` is the MIN across every ancestor, "set" can also mean an
    inherited parent slice or a container root, where "add MemoryHigh" is
    noise the operator can't act on from their own unit. That hint also
    appends a PSI-floor caveat, but only when the floor ISN'T already
    deferring to kernel cgroup-OOM (a deferring host has no floor for the
    throttle's PSI-full rise to trip). Both advisories join with ``"; "``
    and print as one stderr line. The event also carries ``memory_high`` --
    the bounding ancestor's ``memory.high`` soft-throttle threshold
    (``metrics.cgroup_memory_high``), ``None`` when unset -- for the same
    reason: an operator asking "is MemoryHigh even set" is exactly this
    release's field ask.

    A THIRD advisory, ``_UNDELEGATED_HINT``, reads ``brake_memory_high`` --
    the caller-supplied ``[monitor.host_health.brake] memory_high`` config
    switch -- alongside ``delegated``: when the operator turned that brake
    on but this process's own cgroup leaf isn't confirmed delegated
    (``delegated is not True``), the brake would silently write nothing, so
    boot announces it up front even with no memory.max/memory.high
    configured. Independently of the brake, the SAME hint also covers the
    pre-existing "config is informational without delegation" case
    (``delegated is False`` -- CONFIRMED not delegated, never the merely
    unresolved ``None``) when memory_high is already set or the own_scope
    hint above just fired. See the comment block above ``_UNDELEGATED_HINT``
    for why those two triggers use different ``delegated`` strictness.
    Advisory only -- this never changes the operator's cgroup or systemd
    unit."""
    limits = metrics.cgroup_memory_limits()
    swap_total = metrics.swap_total_bytes()
    # Reuse the leaf cgroup_memory_limits already resolved above -- skips
    # re-walking /proc/self/cgroup + the ancestor chain a second time at
    # startup. None (cgroup v2 unavailable) is passed through unchanged:
    # cgroup_memory_high treats a supplied None the same as "not supplied"
    # only via its own default-arg sentinel, and cgroup_memory_limits
    # already returns cgroup_path=None in that exact case, so a bare
    # `self_cgroup=limits["cgroup_path"]` dead-ends at the same place either
    # way as calling with no override would.
    memory_high = metrics.cgroup_memory_high(self_cgroup=limits["cgroup_path"])
    delegated = metrics.cgroup_delegated(self_cgroup=limits["cgroup_path"])
    if brake_memory_high:
        _BRAKE_ARMED_BY_LOG_DIR[log_dir] = delegated is True
    swap_max = limits["memory_swap_max"]
    swap_cap_pct = (
        round(100.0 * swap_max / swap_total, 1) if swap_max is not None and swap_total > 0 else None
    )
    advisories: list[str] = []
    if swap_cap_pct is not None and swap_cap_pct < _SWAP_CAP_ADVISORY_PCT:
        advisories.append(
            "cgroup memory.swap.max is far below host swap; the mid-round floor may "
            "terminate rounds the kernel would have contained -- consider bounding "
            "both memory.max and memory.swap.max, or set "
            "[monitor.host_health.pressure] in_round_terminate=false"
        )
    defer = (
        limits["memory_max"] is not None
        and swap_max is not None
        and limits["memory_max"] < metrics.mem_total_bytes()
        and swap_max <= swap_total  # 4th guard: a >> host-swap cap can't bind -> stay armed
    )
    own_scope = (
        limits["memory_max"] is not None
        and memory_high is None
        and limits["bounding_cgroup_path"] == limits["cgroup_path"]
    )
    if own_scope:
        hint = (
            "memory.max is set on this cgroup without memory.high -- add "
            "memory.high (below memory.max) for graceful throttling before an "
            "OOM kill"
        )
        if not defer:
            hint += (
                "; bound memory.swap.max (at or below host swap) too, or set "
                "[monitor.host_health.pressure] in_round_terminate=false, so the "
                "throttle's PSI-full rise doesn't trip the mid-round floor"
            )
        advisories.append(hint)
    if (brake_memory_high and delegated is not True) or (
        delegated is False and (memory_high is not None or own_scope)
    ):
        advisories.append(_UNDELEGATED_HINT)
    advisory = "; ".join(advisories) or None
    if advisory is not None:
        print(f"agent-runner: {advisory}", file=sys.stderr)
    emit_host_cgroup_memory_limit(
        log_dir,
        memory_max=limits["memory_max"],
        memory_swap_max=swap_max,
        cgroup_path=limits["cgroup_path"],
        defer=defer,
        swap_total_bytes=swap_total,
        swap_cap_pct=swap_cap_pct,
        memory_high=memory_high,
        advisory=advisory,
        cgroup_delegated=delegated,
    )
    return defer
