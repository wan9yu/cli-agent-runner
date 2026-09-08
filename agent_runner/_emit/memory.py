"""Event emission wrappers — host/cgroup memory pressure (mirrors
``cli/_serve_cgroup.py``'s cgroup spine and ``cli/_serve_round.py``'s
host_health mid-round floor). Re-exported from ``agent_runner._emit`` (the
package facade) — see its docstring.
"""

from __future__ import annotations

from pathlib import Path


def emit_round_deferred(log_dir: Path, *, severity: str, signal: str, message: str) -> None:
    """Emit round_deferred when the pre-round admission gate defers the next
    round under host_health memory pressure (Group 3 action half). Paired with
    emit_round_resumed once pressure clears -- like schedule_paused/resumed --
    so a long defer does not trip detect_supervisor_stale (see its suppression
    set in _monitor_detectors.py)."""
    from agent_runner.events import ROUND_DEFERRED, emit

    emit(log_dir, ROUND_DEFERRED, severity=severity, signal=signal, message=message)


def emit_round_resumed(log_dir: Path, *, deferred_for_s: int) -> None:
    """Emit round_resumed when a memory-pressure round deferral clears."""
    from agent_runner.events import ROUND_RESUMED, emit

    emit(log_dir, ROUND_RESUMED, deferred_for_s=deferred_for_s)


def emit_round_mem_terminated(
    log_dir: Path,
    *,
    pid: int,
    severity: str,
    signal: str,
    message: str,
    consecutive: int,
    context: dict,
) -> None:
    """Emit when _spawn_round's mid-round hard floor terminated a ballooning
    round on critical host_health pressure -- the actual coma-preventer (a
    pre-round-only gate can't stop a single round mid-flight). Distinct from
    round_supervisor_wedged (a wall-clock ceiling breach, unrelated cause).

    0.2.16: ``consecutive`` (the critical_streak that crossed the threshold)
    and ``context`` (Pressure.context -- the actual psi/mem numbers, e.g.
    psi_full_avg10) make the kill legible from the event stream alone, so an
    operator can retune host_health thresholds without SSH."""
    from agent_runner.events import ROUND_MEM_TERMINATED, emit

    emit(
        log_dir,
        ROUND_MEM_TERMINATED,
        pid=pid,
        severity=severity,
        signal=signal,
        message=message,
        consecutive=consecutive,
        context=context,
    )


def emit_round_mem_critical_sample(
    log_dir: Path, *, round_num: int, consecutive: int, context: dict
) -> None:
    """Emit on each critical host_health sample inside _spawn_round's mid-round
    hard floor, up to the per-episode cap (0.2.17, below) -- unlike
    round_mem_terminated (deduped to once-per-episode), this fires on every
    critical tick within that cap: the point is calibration visibility into
    near-misses, so an operator watching the event stream sees the
    critical_streak build (1, 2, ...) even on ticks that never reach the
    terminate threshold (a healthy tick resets it before 3-in-a-row).
    ``context`` carries the same Pressure.context numbers as
    round_mem_terminated. Fires only during critical pressure -- never during
    warning/healthy -- so it adds no normal-operation noise, including while
    ``defer_to_cgroup`` steps back from terminating (the streak/context is
    still useful calibration there; this event stays scoped to the
    sample-level signal, distinct from mem_pressure_deferred_to_cgroup's
    once-per-episode terminate-vs-defer notice).

    0.2.17: the caller caps this at ``2 * mem_critical_consecutive_samples``
    consecutive ticks (1..6 at the default 3) -- a sustained-critical
    don't-terminate run (cgroup-defer, or the off switch) would otherwise
    write one event per ~10s tick for up to a whole ``round_timeout_s`` on a
    permanently-deferred/off host. The cap is per streak-episode, not a
    lifetime limit: any non-critical tick still resets the streak to 0, and
    sampling resumes from 1 the next time critical pressure recurs."""
    from agent_runner.events import ROUND_MEM_CRITICAL_SAMPLE, emit

    emit(
        log_dir,
        ROUND_MEM_CRITICAL_SAMPLE,
        round_num=round_num,
        consecutive=consecutive,
        context=context,
    )


def emit_host_cgroup_memory_limit(
    log_dir: Path,
    *,
    memory_max: int | None,
    memory_swap_max: int | None,
    cgroup_path: str | None,
    swap_total_bytes: int | None = None,
    swap_cap_pct: float | None = None,
    memory_high: int | None = None,
    advisory: str | None = None,
) -> None:
    """Emit once at serve startup: this process's cgroup v2 memory budget
    (``metrics.cgroup_memory_limits``). ``None`` fields mean unlimited (or
    cgroup v2 unavailable). Serve uses this once to decide whether the
    mid-round hard floor can defer to kernel cgroup-OOM -- see
    ``emit_mem_pressure_deferred_to_cgroup`` below.

    0.2.18 adds an optional startup swap-cap advisory
    (``swap_total_bytes`` / ``swap_cap_pct`` / ``memory_high`` / ``advisory``)
    as FIELDS on this SAME event -- never a separate event kind, and never an
    auto-change to the operator's cgroup or unit. ``swap_total_bytes`` is the
    host's total swap (``metrics.swap_total_bytes``); ``swap_cap_pct`` is
    ``memory_swap_max`` as a percentage of it (``None`` when either side is
    unknown); ``memory_high`` is the bounding ancestor's ``memory.high`` in
    bytes (``metrics.cgroup_memory_high``) -- ``None`` means unset (the
    cgroup read the literal ``"max"``, or no finite value at all), never the
    raw ``"max"`` token, so a caller can't mistake "unset" for a real
    ceiling; ``advisory`` is the human-readable warning text when the swap
    cap looks implausibly tight, else ``None``."""
    from agent_runner.events import HOST_CGROUP_MEMORY_LIMIT, emit

    emit(
        log_dir,
        HOST_CGROUP_MEMORY_LIMIT,
        memory_max=memory_max,
        memory_swap_max=memory_swap_max,
        cgroup_path=cgroup_path,
        swap_total_bytes=swap_total_bytes,
        swap_cap_pct=swap_cap_pct,
        memory_high=memory_high,
        advisory=advisory,
    )


def emit_round_cgroup_memory(
    log_dir: Path,
    *,
    round_num: int,
    memory_current_peak: int,
    memory_swap_current_peak: int,
    events_high_delta: int,
    events_max_delta: int,
    events_oom_delta: int,
    events_oom_kill_delta: int,
    bounding_cgroup_path: str | None,
) -> None:
    """Emit once per round: the round's peak cgroup ``memory.current`` (+swap) and
    the ``memory.events`` DELTAS (high/max/oom/oom_kill) over the round, read at the
    bounding ancestor. Deltas, never absolutes -- an operator sees pressure BUILDING
    without SSH. Peak is the max over the existing ~10s mid-round tick (NOT
    ``memory.peak``, which is cumulative since cgroup creation). No-op when this
    host has no finite cgroup bound (``_serve_cgroup._emit_round_cgroup_memory``
    never calls this in that case).

    ``bounding_cgroup_path`` is deliberately NOT named ``cgroup_path`` --
    ``host_cgroup_memory_limit`` already uses that name for the LEAF (this
    process's own) cgroup; here it names the BOUNDING ANCESTOR (the one whose
    ``memory.max`` actually binds). Same key, two meanings across the two
    events an operator correlates would be a trap -- events.md pins field
    semantics permanently once shipped, so the distinct name is chosen now."""
    from agent_runner.events import ROUND_CGROUP_MEMORY, emit

    emit(
        log_dir,
        ROUND_CGROUP_MEMORY,
        round_num=round_num,
        memory_current_peak=memory_current_peak,
        memory_swap_current_peak=memory_swap_current_peak,
        events_high_delta=events_high_delta,
        events_max_delta=events_max_delta,
        events_oom_delta=events_oom_delta,
        events_oom_kill_delta=events_oom_kill_delta,
        bounding_cgroup_path=bounding_cgroup_path,
    )


def emit_round_oom_killed(
    log_dir: Path,
    *,
    round_num: int,
    log_path: Path,
    log_bytes: int,
    oom_kill_delta: int,
    partial_log: bool,
) -> None:
    """Emit when the kernel cgroup-OOM-killed a round (``memory.events.oom_kill``
    rose over the round, per ``_serve_cgroup._maybe_emit_oom_killed`` -- folded
    from the SAME ``cgroup_memory_usage()`` read ``round_cgroup_memory`` already
    took, no second sysfs read). Symmetric with ``round_mem_terminated``
    (supervisor-killed) -- this is the kernel-killed sibling.

    POINTER-ONLY: ``log_path`` + ``log_bytes`` + ``oom_kill_delta`` -- NEVER the
    round-log/transcript content itself (durability, secret-redaction, size all
    argue against embedding it in events.jsonl). ``partial_log`` reports whether
    the supervisor's own truncation trailer (see ``_mark_partial_log``) was
    written onto the round log, so a later reader can tell the log's tail is
    supervisor residue, not the agent's own output.

    Classification is UNCHANGED by this event: the round still exits 137 and
    counts toward the crash streak exactly as before -- this is pure
    observability layered on top, computed and emitted after that decision is
    already made."""
    from agent_runner.events import ROUND_OOM_KILLED, emit

    emit(
        log_dir,
        ROUND_OOM_KILLED,
        round_num=round_num,
        log_path=str(log_path),
        log_bytes=log_bytes,
        oom_kill_delta=oom_kill_delta,
        partial_log=partial_log,
    )


def emit_mem_pressure_deferred_to_cgroup(
    log_dir: Path, *, pid: int, signal: str, message: str
) -> None:
    """Emit when _spawn_round's mid-round hard floor hits sustained critical
    pressure but this cgroup's (mem+swap) budget is bounded end to end
    (both memory.max and memory.swap.max finite) -- kernel cgroup-OOM will
    contain the agent and keep the host responsive on its own, so the
    cruder host-wide round-kill steps back instead of firing
    round_mem_terminated. This OVERRIDES in_round_mem_terminate=True: a
    bounded cgroup makes the host floor strictly worse, not just redundant."""
    from agent_runner.events import MEM_PRESSURE_DEFERRED_TO_CGROUP, emit

    emit(log_dir, MEM_PRESSURE_DEFERRED_TO_CGROUP, pid=pid, signal=signal, message=message)
