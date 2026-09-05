"""Event emission wrappers — extracted from api.py for module-size hygiene.

All wrappers are re-exported from agent_runner.api for backward compatibility.
Plugins and supervisor should continue importing from agent_runner.api
(e.g. ``from agent_runner.api import emit_transient_error_detected``).

Each wrapper exists to keep cli/serve_cmd.py from importing agent_runner.events
directly — preserves the 0.1.21 architecture invariant. Local-import pattern
inside each wrapper body keeps agent_runner.api import-cheap.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "emit_agent_auth_error_detected",
    "emit_agent_usage_recorded",
    "emit_anomaly_repetitive_tool",
    "emit_config_migrated",
    "emit_fresh_eyes_round_triggered",
    "emit_host_cgroup_memory_limit",
    "emit_max_rounds_reached",
    "emit_mem_loop",
    "emit_mem_loop_persistent",
    "emit_mem_pressure_deferred_to_cgroup",
    "emit_rate_limit_stop",
    "emit_round_deferred",
    "emit_round_grace_extended",
    "emit_round_grace_kill",
    "emit_round_logs_prune_deferred",
    "emit_round_mem_critical_sample",
    "emit_round_mem_terminated",
    "emit_round_progress",
    "emit_round_resumed",
    "emit_round_substrate_after",
    "emit_round_substrate_before",
    "emit_round_supervisor_wedged",
    "emit_schedule_paused",
    "emit_schedule_phase_skipped",
    "emit_schedule_resumed",
    "emit_stale_index_lock_cleared",
    "emit_stalled_no_progress",
    "emit_stop_file_detected",
    "emit_transient_error_backoff_capped",
    "emit_transient_error_detected",
    "emit_transient_error_recovered",
]


def emit_rate_limit_stop(log_dir: Path) -> None:
    """Emit ``agent_self_terminated`` with reason ``rate_limit`` (serve_cmd wrapper)."""
    from agent_runner import events

    events.emit(log_dir, events.SELF_TERMINATED, reason="rate_limit")


def emit_max_rounds_reached(log_dir: Path, *, rounds_completed: int, max_rounds: int) -> None:
    """Emit max_rounds_reached event (serve_cmd wrapper; avoids direct events import)."""
    from agent_runner.events import MAX_ROUNDS_REACHED, emit

    emit(log_dir, MAX_ROUNDS_REACHED, rounds_completed=rounds_completed, max_rounds=max_rounds)


def emit_config_broken(log_dir: Path, *, reason: str) -> None:
    """Emit config_broken (serve gave up on a permanent, non-self-healing failure —
    a startup-battery check, or any other ConfigError-classified round exit)."""
    from agent_runner.events import CONFIG_BROKEN, emit

    emit(log_dir, CONFIG_BROKEN, reason=reason)


def _emit_giveup(
    log_dir: Path, kind: str, *, consecutive: int, exit_code: int, log_path: Path
) -> None:
    """Shared body for the give-up events (serve stopped/broke after consecutive
    failures of one kind): capture a redacted tail of the round log as ``reason``
    so the recurring failure can be inspected (or later classified into a
    transient bucket), and emit ``kind`` with the standard give-up payload.
    """
    from agent_runner._redact import redact_secrets
    from agent_runner.events import emit

    try:
        reason = redact_secrets(log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
    except OSError:
        reason = ""
    emit(log_dir, kind, consecutive=consecutive, exit_code=exit_code, reason=reason)


def emit_crash_loop(log_dir: Path, *, consecutive: int, exit_code: int, log_path: Path) -> None:
    """Emit crash_loop (serve stopped after consecutive unknown short crashes).

    Captures the failure reason — a redacted tail of the round log — so a
    recurring unknown crash can later be classified into a transient bucket.
    """
    from agent_runner.events import CRASH_LOOP

    _emit_giveup(
        log_dir, CRASH_LOOP, consecutive=consecutive, exit_code=exit_code, log_path=log_path
    )


def emit_mem_loop(log_dir: Path, *, consecutive: int, exit_code: int, log_path: Path) -> None:
    """Emit mem_loop (serve gave up after consecutive mid-round memory-pressure
    terminations — the 0.2.15 coma-preventer's give-up cap). Distinct from
    crash_loop: this is a break-then-restart, not a deliberate stop, so it is
    deliberately absent from the unit's RestartPreventExitStatus.

    Captures the failure reason — a redacted tail of the round log — same as
    crash_loop, so an operator can see what the round was doing when the host
    ran out of memory."""
    from agent_runner.events import MEM_LOOP

    _emit_giveup(log_dir, MEM_LOOP, consecutive=consecutive, exit_code=exit_code, log_path=log_path)


def emit_mem_loop_persistent(
    log_dir: Path, *, consecutive: int, exit_code: int, log_path: Path
) -> None:
    """Emit mem_loop_persistent (serve STOPS for real — 0.2.16 Task 5 cross-restart
    convergence: mem_loop itself kept recurring across restarts within the
    escalation window, so systemd is told to stop rather than respawn into the
    identical loop forever). Distinct from mem_loop: this IS a deliberate stop,
    like crash_loop/config_broken, so it belongs in the unit's
    RestartPreventExitStatus.

    ``consecutive`` counts mem_loop EPISODES within the persistence window
    (up to ``_serve_policy._MEM_LOOP_PERSIST_THRESHOLD``) — NOT mid-round
    terminations within one episode, which is mem_loop's own ``consecutive``
    (up to ``MEM_LOOP_THRESHOLD``). Same redacted-log-tail reason capture as
    crash_loop/mem_loop."""
    from agent_runner.events import MEM_LOOP_PERSISTENT

    _emit_giveup(
        log_dir,
        MEM_LOOP_PERSISTENT,
        consecutive=consecutive,
        exit_code=exit_code,
        log_path=log_path,
    )


def emit_stalled_no_progress(
    log_dir: Path, *, consecutive: int, exit_code: int, log_path: Path
) -> None:
    """Emit stalled_no_progress (serve gave up after consecutive clean-but-
    no-progress rounds -- 0.2.16 Task 6). A round that exits 0 fast with no
    ``agent_usage_recorded`` never reached the model (pi, and CLIs like it,
    exit 0 on a provider failure) -- ``_round_ok = exit_code == 0``
    (api_types.py) reads that as clean, so without this breaker it is a fast,
    invisible, unclassified-failure spin, no different in kind from an
    unknown short crash except for the exit code it hides behind.

    Deliberately reuses ``CRASH_LOOP_EXIT`` (75), not a new exit code: this is
    the SAME give-up verdict as crash_loop ("an unknown failure kept
    recurring, stop for real") reached via a different signal (no usage
    instead of a non-zero exit) -- not a new failure class, so it needs no new
    entry in the systemd unit's ``RestartPreventExitStatus``. Same redacted-
    log-tail reason capture as crash_loop/mem_loop."""
    from agent_runner.events import STALLED_NO_PROGRESS

    _emit_giveup(
        log_dir,
        STALLED_NO_PROGRESS,
        consecutive=consecutive,
        exit_code=exit_code,
        log_path=log_path,
    )


def emit_config_migrated(
    log_dir: Path, *, applied: list[str], manual: list[str], path: str
) -> None:
    """Emit config_migrated when `migrate`/`upgrade` rewrites the config."""
    from agent_runner.events import CONFIG_MIGRATED, emit

    emit(log_dir, CONFIG_MIGRATED, applied=applied, manual=manual, path=path)


def emit_stop_file_detected(
    log_dir: Path, *, stop_file: Path, content: str, rounds_completed: int
) -> None:
    """Centralises emission so cli/serve_cmd.py need not import agent_runner.events directly."""
    from agent_runner.events import STOP_FILE_DETECTED, emit

    emit(
        log_dir,
        STOP_FILE_DETECTED,
        stop_file=str(stop_file),
        content=content,
        rounds_completed=rounds_completed,
    )


def emit_stale_index_lock_cleared(log_dir: Path, *, lock_path: str, round_num: int) -> None:
    """Emit when serve removed a .git/index.lock that its own timed-out+killed git
    call orphaned. Single-writer: serve holds the round lock, so any lock surviving
    our kill is ours to clear."""
    from agent_runner.events import STALE_INDEX_LOCK_CLEARED, emit

    emit(log_dir, STALE_INDEX_LOCK_CLEARED, lock_path=lock_path, round_num=round_num)


def emit_schedule_paused(
    log_dir: Path, *, active_window: str, resume_at: str, timezone: str, phase: str = ""
) -> None:
    """Emit schedule_paused when the serve loop enters a configured pause window.

    ``phase`` is the phase the supervisor is waiting for on a phase-aware pause;
    it is omitted from the payload when empty so the legacy (non-phase) pause
    stays byte-identical to 0.2.7."""
    from agent_runner.events import SCHEDULE_PAUSED, emit

    fields = {"active_window": active_window, "resume_at": resume_at, "timezone": timezone}
    if phase:
        fields["phase"] = phase
    emit(log_dir, SCHEDULE_PAUSED, **fields)


def emit_schedule_phase_skipped(
    log_dir: Path, *, round_num: int, skipped: list[str], chosen: str | None, active_window: str
) -> None:
    """Emit schedule_phase_skipped when phase_policy=skip steps over closed phases
    to reach the first runnable one this round."""
    from agent_runner.events import SCHEDULE_PHASE_SKIPPED, emit

    emit(
        log_dir,
        SCHEDULE_PHASE_SKIPPED,
        round_num=round_num,
        skipped=skipped,
        chosen=chosen,
        active_window=active_window,
    )


def emit_schedule_resumed(log_dir: Path, *, paused_for_s: int) -> None:
    """Emit schedule_resumed when the serve loop exits a pause window."""
    from agent_runner.events import SCHEDULE_RESUMED, emit

    emit(log_dir, SCHEDULE_RESUMED, paused_for_s=paused_for_s)


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
    log_dir: Path, *, memory_max: int | None, memory_swap_max: int | None, cgroup_path: str | None
) -> None:
    """Emit once at serve startup: this process's cgroup v2 memory budget
    (``metrics.cgroup_memory_limits``). ``None`` fields mean unlimited (or
    cgroup v2 unavailable). Serve uses this once to decide whether the
    mid-round hard floor can defer to kernel cgroup-OOM -- see
    ``emit_mem_pressure_deferred_to_cgroup`` below."""
    from agent_runner.events import HOST_CGROUP_MEMORY_LIMIT, emit

    emit(
        log_dir,
        HOST_CGROUP_MEMORY_LIMIT,
        memory_max=memory_max,
        memory_swap_max=memory_swap_max,
        cgroup_path=cgroup_path,
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


def emit_round_substrate_before(
    log_dir: Path, *, round_num: int, git_head: str | None, paths_hash: str | None
) -> None:
    """Emit pre-round substrate fingerprint (git HEAD + optional file hash)."""
    from agent_runner.events import ROUND_SUBSTRATE_BEFORE, emit

    emit(
        log_dir,
        ROUND_SUBSTRATE_BEFORE,
        round_num=round_num,
        git_head=git_head,
        paths_hash=paths_hash,
    )


def emit_round_substrate_after(
    log_dir: Path, *, round_num: int, git_head: str | None, paths_hash: str | None
) -> None:
    """Emit post-round substrate fingerprint (git HEAD + optional file hash)."""
    from agent_runner.events import ROUND_SUBSTRATE_AFTER, emit

    emit(
        log_dir,
        ROUND_SUBSTRATE_AFTER,
        round_num=round_num,
        git_head=git_head,
        paths_hash=paths_hash,
    )


def emit_round_supervisor_wedged(
    log_dir: Path, *, pid: int, timeout_s: int, log_path: Path
) -> None:
    """Emit when the round subprocess blew past the outer ceiling and serve had to
    TERM/kill it (the round supervisor was wedged — not doing its own bounded
    post-round cleanup). Distinct from round_timeout_kill (the AGENT hit the inner
    wall inside a healthy round)."""
    from agent_runner.events import ROUND_SUPERVISOR_WEDGED, emit

    emit(log_dir, ROUND_SUPERVISOR_WEDGED, pid=pid, timeout_s=timeout_s, log_path=str(log_path))


def emit_fresh_eyes_round_triggered(log_dir: Path, *, round_num: int, every_n: int) -> None:
    """Emit fresh-eyes signal trigger event (only on triggered rounds)."""
    from agent_runner.events import FRESH_EYES_ROUND_TRIGGERED, emit

    emit(
        log_dir,
        FRESH_EYES_ROUND_TRIGGERED,
        round_num=round_num,
        every_n=every_n,
    )


def emit_transient_error_detected(
    log_dir: Path,
    *,
    classification: str,
    agent: str,
    reset_at_epoch: int,
    round_num: int,
    raw: str,
    phase: str = "",
) -> None:
    """Emit detection of a transient agent error (rate limit / 5xx / timeout).

    ``phase`` is the rotation phase the failing round ran under ("" when the
    config has no ``[phases]``). It lets a ``phase_policy = "skip"`` serve loop
    step over *this* phase while a healthy sibling keeps running."""
    from agent_runner._redact import redact_secrets
    from agent_runner.events import TRANSIENT_ERROR_DETECTED, emit

    raw = redact_secrets(raw)
    emit(
        log_dir,
        TRANSIENT_ERROR_DETECTED,
        classification=classification,
        agent=agent,
        reset_at_epoch=reset_at_epoch,
        round_num=round_num,
        raw=raw,
        phase=phase,
    )


def emit_agent_auth_error_detected(
    log_dir: Path,
    *,
    round_num: int,
    agent: str,
    raw: str,
) -> None:
    """Emit an authentication/authorization failure the agent itself reported.

    Contract: emitted by a per-CLI plugin only when the agent's OWN structured
    output names the failure (e.g. an HTTP 401 in its JSON event stream). That
    is certain evidence, unlike the monitor's ``oauth_fail`` text heuristic,
    which scans free-text log tails and therefore needs a nonzero-exit shield
    against prose that merely mentions "401". The monitor counts a round
    carrying this event without that shield — which is what makes an auth loop
    visible for a CLI that exits 0 on provider failure.

    No back-off partner event: an auth failure is permanent until an operator
    fixes the credential, so it is deliberately not a transient classification.
    """
    from agent_runner._redact import redact_secrets
    from agent_runner.events import AGENT_AUTH_ERROR_DETECTED, emit

    emit(
        log_dir,
        AGENT_AUTH_ERROR_DETECTED,
        round_num=round_num,
        agent=agent,
        raw=redact_secrets(raw),
    )


def emit_transient_error_recovered(
    log_dir: Path,
    *,
    classification: str,
    agent: str,
    throttled_for_s: int,
) -> None:
    """Emit recovery from a transient error back-off (right before resuming)."""
    from agent_runner.events import TRANSIENT_ERROR_RECOVERED, emit

    emit(
        log_dir,
        TRANSIENT_ERROR_RECOVERED,
        classification=classification,
        agent=agent,
        throttled_for_s=throttled_for_s,
    )


def emit_agent_usage_recorded(
    log_dir: Path,
    *,
    agent: str,
    model: str,
    round_num: int,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    cost_usd: float | None,
    duration_ms: int,
    models_breakdown: dict[str, dict[str, int]] | None = None,
    cache_creation_tokens: int = 0,
    tool_call_count: int = 0,
    phase: str = "",
    success: bool = True,
) -> None:
    """Emit per-round usage record from a CLI plugin.

    Raw data only — aggregation (totals, projections, budget warnings) is
    deferred to consumers and the 0.1.25 capability layer.

    Field semantics:
    - ``input_tokens``: net non-cached input (both claude and gemini emit net;
      total throughput = input_tokens + cached_tokens).
    - ``cost_usd``: USD cost for the round, or None when CLI doesn't expose it
      (gemini has no cost field; claude exposes total_cost_usd).
    - ``models_breakdown``: only populated when a round used multiple models
      (gemini multi-model rounds). None for claude (always single-model).
    - ``cache_creation_tokens``: claude only — ``usage.cache_creation_input_tokens``,
      independent count from ``cached_tokens`` (cache_read). Priced differently
      from fresh input by the provider; the framework records the count only and
      never interprets cost (no price tables here — see ``cost_usd`` above for the
      one cost figure the framework passes through verbatim). Gemini has no
      equivalent → 0.
    - ``tool_call_count``: number of tool invocations the agent made in the round.
      Claude: count of ``tool_use`` content blocks across all assistant events.
      Gemini: ``stats.tool_calls``.
    - ``phase``: phase label from HookContext (e.g. "planning"); empty string when None.
    - ``success``: the supervisor's clean-exit predicate (``RoundResult.ok``).
      A plugin MAY additionally fold in the agent's own terminal verdict when
      its CLI's exit code is unreliable — pi does, because pi exits 0 on
      provider failure.
    """
    from agent_runner.events import AGENT_USAGE_RECORDED, emit

    emit(
        log_dir,
        AGENT_USAGE_RECORDED,
        agent=agent,
        model=model,
        round_num=round_num,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        models_breakdown=models_breakdown,
        cache_creation_tokens=cache_creation_tokens,
        tool_call_count=tool_call_count,
        phase=phase,
        success=success,
    )


def emit_round_progress(
    log_dir: Path,
    *,
    round_num: int,
    log_size_kb: int,
    last_write_age_s: int,
    wall_age_s: int,
) -> None:
    """Mid-round heartbeat event when round_progress_interval_s > 0.

    Emitted periodically during a live round to surface visibility on long
    rounds: log_size_kb shows writing activity; last_write_age_s and wall_age_s
    together distinguish "agent thinking" from "agent stuck".
    """
    from agent_runner.events import ROUND_PROGRESS, emit

    emit(
        log_dir,
        ROUND_PROGRESS,
        round_num=round_num,
        log_size_kb=log_size_kb,
        last_write_age_s=last_write_age_s,
        wall_age_s=wall_age_s,
    )


def emit_round_grace_kill(
    log_dir: Path,
    *,
    round_num: int,
    grace_s: int,
    live_children: list[dict] | None = None,
) -> None:
    """Emit when the subprocess was killed because the grace-after-result timer
    expired AND the agent's process group had no live worker processes left
    (a genuine hang). Distinct from round_grace_extended (grace elapsed but a
    worker was still running) and round_timeout_kill (wall-clock exceeded).

    live_children: list of ``{"name": <exe basename>, "pid": <int>}`` dicts
        (0.1.40+; previously list of cmdline strings).
    """
    from agent_runner.events import ROUND_GRACE_KILL, emit

    emit(
        log_dir,
        ROUND_GRACE_KILL,
        round_num=round_num,
        grace_s=grace_s,
        live_children=live_children or [],
    )


def emit_round_grace_extended(
    log_dir: Path,
    *,
    round_num: int,
    grace_s: int,
    live_children: list[dict],
    ignored_children: list[dict] | None = None,
) -> None:
    """Emit when the grace-after-result timer expired but the agent still had
    live worker processes (e.g. a backgrounded build), so the round was NOT
    killed; it continues until it finishes or hits round_timeout_s.

    live_children: list of ``{"name": <exe basename>, "pid": <int>}`` dicts
        (0.1.40+; previously list of cmdline strings).
    ignored_children: list of ``{"name": ..., "pid": ..., "matched": <pattern>}``
        dicts for children that matched a grace_kill_ignore_patterns entry
        and were excluded from the liveness count (0.1.40+; previously cmdline strings).
    """
    from agent_runner.events import ROUND_GRACE_EXTENDED, emit

    emit(
        log_dir,
        ROUND_GRACE_EXTENDED,
        round_num=round_num,
        grace_s=grace_s,
        live_children=live_children,
        ignored_children=ignored_children or [],
    )


def emit_round_logs_prune_deferred(
    log_dir: Path,
    *,
    directory: str,
    existing: int,
    keep: int,
    would_delete: int,
) -> None:
    """Emit when a round-log prune was deferred because it would be *bulk*
    (it would delete more files than it keeps), so nothing was deleted.

    ``directory`` is the directory holding the family — ``{log_dir}/rounds``
    for agent transcripts, ``{log_dir}`` for the serve-level ``round-<N>.log``
    files. Re-emitted on every prune attempt while the condition holds: the
    deferral is permanent until an operator acts, and a one-shot event would
    be missed by anyone who started watching later.

    The hint is composed here rather than at the call sites so both families
    name the same knob with the same wording.
    """
    from agent_runner.events import ROUND_LOGS_PRUNE_DEFERRED, emit

    emit(
        log_dir,
        ROUND_LOGS_PRUNE_DEFERRED,
        directory=directory,
        existing=existing,
        keep=keep,
        would_delete=would_delete,
        hint=(
            f"nothing deleted; raise runtime.round_log_retention to >= {existing} "
            f"to keep this backlog, or delete files in {directory} yourself"
        ),
    )


def emit_anomaly_repetitive_tool(
    log_dir: Path,
    *,
    round_num: int,
    tool_name: str,
    target: str | None,
    count: int,
    window: int,
) -> None:
    """Emit when claude plugin detects the same (tool, target) tuple repeated
    >= threshold times in a sliding window of tool-call events.

    Claude-only (gemini JSONL stats summary does not expose per-tool events).
    Default OFF: both anomaly_repetitive_window and anomaly_repetitive_threshold
    must be > 0 in [monitor] config to activate.
    """
    from agent_runner.events import ANOMALY_REPETITIVE_TOOL, emit

    emit(
        log_dir,
        ANOMALY_REPETITIVE_TOOL,
        round_num=round_num,
        tool_name=tool_name,
        target=target,
        count=count,
        window=window,
    )


def emit_transient_error_backoff_capped(
    log_dir: Path,
    *,
    classification: str,
    agent: str,
    requested_sleep_s: int,
    applied_sleep_s: int,
    original_reset_at_epoch: int | None = None,
    applied_reset_at_epoch: int | None = None,
    consecutive_count: int | None = None,
    capped_by_absolute_max: bool | None = None,
) -> None:
    """Emit when supervisor adjusts the plugin-emitted transient back-off.

    Fires in two cases:
    1. **Exp backoff applied** (0.1.33+): estimated-class transient errors
       (`rate_limit_model` / `api_transient_5xx` / `api_timeout`) doubled
       on consecutive failures. ``consecutive_count`` > 1, multiplier > 1×.
    2. **Defensive cap hit** (0.1.20+): malformed `reset_at_epoch` or the
       30-min absolute cap clipped the wait. ``capped_by_absolute_max`` True.

    Fields ``original_reset_at_epoch`` / ``applied_reset_at_epoch`` /
    ``consecutive_count`` / ``capped_by_absolute_max`` are 0.1.33+. Older
    callers that pass only the first 4 kwargs continue to work; the new
    fields are omitted from the payload when None.
    """
    from agent_runner.events import TRANSIENT_ERROR_BACKOFF_CAPPED, emit

    kwargs: dict = {
        "classification": classification,
        "agent": agent,
        "requested_sleep_s": requested_sleep_s,
        "applied_sleep_s": applied_sleep_s,
    }
    if original_reset_at_epoch is not None:
        kwargs["original_reset_at_epoch"] = original_reset_at_epoch
    if applied_reset_at_epoch is not None:
        kwargs["applied_reset_at_epoch"] = applied_reset_at_epoch
    if consecutive_count is not None:
        kwargs["consecutive_count"] = consecutive_count
    if capped_by_absolute_max is not None:
        kwargs["capped_by_absolute_max"] = capped_by_absolute_max

    emit(log_dir, TRANSIENT_ERROR_BACKOFF_CAPPED, **kwargs)
