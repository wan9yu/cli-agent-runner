"""Main round orchestration. Conducts the other modules; does not touch
subprocess / git / prompt details directly. Pure rotation — no event-driven
branches based on prior round state (§7 IMMUTABLE).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import sys
import time
import traceback as tb_mod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import agent_runner.api as api
from agent_runner import (
    agent_runtime,
    context_store,
    events,
    hooks,
    metrics,
    startup_check,
    vcs_state,
)
from agent_runner.api import _primary_prompt_file, resolve_runtime_for_phase
from agent_runner.api import assemble_prompt as _api_assemble_prompt
from agent_runner.api_types import RoundResult, TransientErrorState
from agent_runner.config import Config
from agent_runner.events import (
    AGENT_NETWORK_BLIP,
    now_iso_ms,
    parse_iso_ms,
)
from agent_runner.monitor import NETWORK_PATTERNS

_BACK_OFF_CAP_S = 28800  # 8h — defensive cap; 1.6× the 5h-window
_BACK_OFF_JITTER_MIN_S = 5
_BACK_OFF_JITTER_MAX_S = 30


def _apply_back_off(log_dir: Path, throttle: TransientErrorState) -> None:
    """Sleep until adjusted reset_at + jitter; emit recovered (and capped if applicable).

    For estimated-class classifications (rate_limit_model / api_transient_5xx /
    api_timeout), applies exp backoff on consecutive failures via
    `_throttle.compute_adjusted_reset_at`. For server-authoritative
    rate_limit_account, the original reset_at_epoch is used verbatim.

    Defensive 8h cap retained as last-line defense against malformed reset
    epochs (e.g. an external/manual event with a far-future reset_at).
    """
    from agent_runner import _throttle

    adjusted_reset_at, _consecutive_count, _capped = _throttle.compute_adjusted_reset_at(
        classification=throttle.classification,
        original_reset_at_epoch=throttle.reset_at_epoch,
        agent=throttle.agent,
        log_dir=log_dir,
    )

    now = time.time()
    requested = (
        adjusted_reset_at - now + random.uniform(_BACK_OFF_JITTER_MIN_S, _BACK_OFF_JITTER_MAX_S)
    )
    if requested > _BACK_OFF_CAP_S:
        # Defensive: malformed reset epoch (e.g. manual event with far-future ts).
        # Exp backoff layer caps at 30min, so legitimate flow never hits this.
        api.emit_transient_error_backoff_capped(
            log_dir,
            classification=throttle.classification,
            agent=throttle.agent,
            requested_sleep_s=int(requested),
            applied_sleep_s=_BACK_OFF_CAP_S,
        )
        sleep_s = _BACK_OFF_CAP_S
    else:
        sleep_s = max(requested, 0.0)

    sleep_start = time.time()
    time.sleep(sleep_s)

    api.emit_transient_error_recovered(
        log_dir,
        classification=throttle.classification,
        agent=throttle.agent,
        throttled_for_s=int(time.time() - sleep_start),
    )


class LockHeldError(RuntimeError):
    pass


def _holder_sidecar(lock_path: Path) -> Path:
    return lock_path.parent / (lock_path.name + ".holder")


def _read_cmdline(pid: int) -> str:
    """Read /proc/<pid>/cmdline; return first 80 chars, nulls replaced with spaces."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError):
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()[:80]


def _write_holder_sidecar(lock_path: Path) -> None:
    """Write JSON sidecar describing the current lock holder."""
    payload = {
        "pid": os.getpid(),
        "started_at": now_iso_ms(),
        "cmdline": _read_cmdline(os.getpid()),
    }
    _holder_sidecar(lock_path).write_text(json.dumps(payload), encoding="utf-8")


def _format_holder_msg(lock_path: Path) -> str:
    """Read the sidecar and format a human-readable holder description."""
    sidecar = _holder_sidecar(lock_path)
    data = context_store.read_json(sidecar)
    if data is None:
        return "holder unknown, sidecar missing"
    if not isinstance(data, dict):
        return "holder info unreadable"

    pid = data.get("pid")
    started_at = data.get("started_at", "")
    cmdline = data.get("cmdline", "")

    if not isinstance(pid, int):
        return "holder info unreadable"

    try:
        os.kill(pid, 0)  # check liveness
    except ProcessLookupError:
        return f"stale sidecar, holder PID {pid} no longer alive"
    except PermissionError:
        # PID exists but owned by someone else — still useful info
        pass

    age_s = ""
    try:
        started = parse_iso_ms(started_at)
        age = (datetime.now(UTC) - started).total_seconds()
        age_s = f"{age:.0f}s"
    except (ValueError, TypeError):
        age_s = "?"

    return f"held by PID {pid}, age {age_s}, cmd: {cmdline}"


def _acquire_lock_or_raise(lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        os.close(fd)
        holder_msg = _format_holder_msg(lock_path)
        raise LockHeldError(f"another agent-runner is holding {lock_path} ({holder_msg})") from e
    _write_holder_sidecar(lock_path)
    return fd


def _phase_for(
    round_num: int,
    phases: list[str] | None,
    override: str | None = None,
) -> tuple[str | None, int]:
    """Pick the phase for this round.

    Default: round-number-modulo rotation across phases. Explicit override
    bypasses the counter (used by `agent-runner round --phase NAME` for audit /
    debug / multi-script orchestration). Override does NOT mutate the counter —
    subsequent default rounds resume normal rotation.
    """
    if override is not None:
        if not phases:
            raise ValueError("--phase requires [phases] to be configured in agent-runner.toml")
        if override not in phases:
            raise ValueError(f"phase {override!r} not in configured [phases]: {phases}")
        return override, phases.index(override)
    if not phases:
        return None, 0
    idx = (round_num - 1) % len(phases)
    return phases[idx], idx


def _previous_block(prev: context_store.Status | None, dirty_last: bool) -> dict[str, Any] | None:
    if prev is None:
        return None
    return {
        "exit_code": prev.last_exit_code,
        "duration_s": prev.last_duration_s,
        "ended_at": prev.last_completed_at,
        "had_dirty_tree": dirty_last,
    }


def _round_context_for_prompt(
    round_num: int,
    started_at: str,
    phase: str | None,
    orphan_block: dict[str, Any] | None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {"round_num": round_num, "started_at": started_at}
    if phase is not None:
        ctx["phase"] = phase
    if orphan_block is not None:
        ctx["orphan_stash"] = orphan_block
    return ctx


def _scan_round_log_for_network_blip(
    *,
    log_dir: Path,
    log_path: Path,
    result: agent_runtime.RunResult,
    round_num: int,
    phase: str | None,
) -> None:
    """Scan the round log for network-error patterns; emit one agent_network_blip
    if any match. One blip per round (first match only); see NETWORK_PATTERNS in
    monitor.py for the regex.
    """
    # Network blips almost exclusively manifest with non-zero exit or timeout.
    # Skip the I/O on the success path.
    if result.exit_code == 0 and not result.timed_out:
        return
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return
    m = NETWORK_PATTERNS.search(text)
    if m is None:
        return
    events.emit(
        log_dir,
        AGENT_NETWORK_BLIP,
        round_num=round_num,
        phase=phase,
        matched=m.group(0),
        round_duration_s=result.duration_s,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
    )


def _stitch_enricher_slices(
    base: dict[str, Any],
    enrichers: list,
    hook_ctx: hooks.HookContext,
    log_dir: Path,
) -> dict[str, Any]:
    """Merge each enricher's slice under ``base[enricher.name]``.

    Any exception is caught and emitted as a ``hook_failed`` event; the
    round continues with whatever slices succeeded.
    """
    out = dict(base)
    for enricher in enrichers:
        try:
            out[enricher.name] = enricher.enrich(hook_ctx)
        except Exception as exc:
            payload = hooks._summarize_error(exc, tb=tb_mod.format_exc())
            events.emit(
                log_dir,
                events.HOOK_FAILED,
                hook_name=enricher.name,
                hook_kind="context_enricher",
                **payload,
            )
    return out


def _run_pre_round_hooks(
    hook_ctx: hooks.HookContext,
    log_dir: Path,
    *,
    disabled: bool = False,
    prompt_file: Path | None = None,
) -> None:
    """Invoke registered PreRoundHook plugins. Failures are isolated.

    When ``disabled=True`` (from ``cfg.runtime.disable_pre_round_hooks``),
    skip all hooks. PostRoundHooks are unaffected.

    When ``prompt_file`` is provided, the content is hashed before and after
    EACH hook; mutations emit ``prompt_overwritten`` events with hook attribution.
    Multiple mutating hooks per round → multiple events (audit trail).
    """
    if disabled:
        return

    def _file_sha256(p: Path) -> str:
        try:
            return hashlib.sha256(p.read_bytes()).hexdigest()
        except FileNotFoundError:
            return ""

    prev_hash = _file_sha256(prompt_file) if prompt_file is not None else ""

    for hook in hooks.pre_round_hooks():
        try:
            hook.before_round(hook_ctx)
        except Exception as exc:
            payload = hooks._summarize_error(exc, tb=tb_mod.format_exc())
            events.emit(
                log_dir,
                events.HOOK_FAILED,
                hook_name=hook.name,
                hook_kind="pre_round",
                **payload,
            )
            continue  # don't compare prompt hash for crashed hooks

        if prompt_file is not None:
            new_hash = _file_sha256(prompt_file)
            if new_hash != prev_hash:
                events.emit(
                    log_dir,
                    events.PROMPT_OVERWRITTEN,
                    round_num=hook_ctx.round_num,
                    phase=hook_ctx.phase,
                    hook=hook.name,
                    prompt_path=str(prompt_file),
                    old_hash=f"sha256:{prev_hash}",
                    new_hash=f"sha256:{new_hash}",
                )
                prev_hash = new_hash


def _run_post_round_hooks(
    hook_ctx: hooks.HookContext,
    result: RoundResult,
    log_dir: Path,
) -> None:
    """Invoke registered PostRoundHook plugins. Failures are isolated."""
    for hook in hooks.post_round_hooks():
        try:
            hook.after_round(hook_ctx, result)
        except Exception as exc:
            payload = hooks._summarize_error(exc, tb=tb_mod.format_exc())
            events.emit(
                log_dir,
                events.HOOK_FAILED,
                hook_name=hook.name,
                hook_kind="post_round",
                **payload,
            )


def run_one_round(cfg: Config, *, phase_override: str | None = None) -> RoundResult:
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    # L3: startup precondition battery (R721 + #446 defense)
    failures = [r for r in startup_check.run_battery(cfg) if not r.ok]
    if failures:
        for r in failures:
            print(
                f"STARTUP FAIL: {r.name}: {r.reason} | how-to-fix: {r.how_to_fix}",
                file=sys.stderr,
            )
            events.emit(log_dir, "smoke_check_failed", reason=f"{r.name}: {r.reason}")
        sys.exit(1)

    # Concurrency lock (per-project)
    lock_path = log_dir / "agent-runner.lock"
    lock_fd = _acquire_lock_or_raise(lock_path)
    try:
        return _run_one_round_inner(cfg, phase_override=phase_override)
    finally:
        os.close(lock_fd)
        _holder_sidecar(lock_path).unlink(missing_ok=True)


def _run_one_round_inner(cfg: Config, *, phase_override: str | None = None) -> RoundResult:
    log_dir = cfg.runtime.log_dir

    prev_status = context_store.read_status(log_dir)
    if (log_dir / "status.json").exists() and prev_status is None:
        events.emit(log_dir, "status_recovered", reason="status.json could not be parsed")

    round_num = (prev_status.round_num if prev_status else 0) + 1
    phase, phase_idx = _phase_for(round_num, cfg.phases.list, override=phase_override)
    resolved_rt = resolve_runtime_for_phase(cfg, phase)
    timeout_s = resolved_rt.round_timeout_s
    started_at = now_iso_ms()

    orphan = context_store.read_orphan_state(log_dir)
    orphan_block: dict[str, Any] | None = None
    if orphan and orphan.stashed_ref:
        orphan_block = {
            "ref": orphan.stashed_ref,
            "message": orphan.stash_message,
            "files": orphan.files,
        }

    previous_block = _previous_block(prev_status, dirty_last=bool(orphan))

    base_ctx = _round_context_for_prompt(round_num, started_at, phase, orphan_block)

    rounds_dir = log_dir / "rounds"
    rounds_dir.mkdir(exist_ok=True)
    log_path = rounds_dir / f"R{round_num}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}.log"

    hook_ctx = hooks.HookContext(
        work_dir=cfg.runtime.work_dir,
        log_dir=log_dir,
        project=cfg.runtime.work_dir.resolve().name or "default",
        round_num=round_num,
        phase=phase,
        agent_name=cfg.agent.name or (cfg.agent.command[0] if cfg.agent.command else None),
        agent_binary=Path(cfg.agent.command[0]).name if cfg.agent.command else None,
        agent_log_path=log_path,
        dry_run=cfg.runtime.dry_run,
        anomaly_repetitive_window=cfg.monitor.anomaly_repetitive_window,
        anomaly_repetitive_threshold=cfg.monitor.anomaly_repetitive_threshold,
    )
    _run_pre_round_hooks(
        hook_ctx,
        log_dir,
        disabled=resolved_rt.disable_pre_round_hooks,
        prompt_file=_primary_prompt_file(cfg),
    )
    enriched_ctx = _stitch_enricher_slices(base_ctx, hooks.context_enrichers(), hook_ctx, log_dir)

    # Merge the previous/orphan blocks BEFORE writing (preserving prior behavior)
    if previous_block is not None:
        enriched_ctx["previous"] = previous_block
    if orphan_block is not None:
        enriched_ctx["orphan_stash"] = orphan_block

    # Write the FULL enriched context to round-context.json
    context_store.atomic_write_json(log_dir / context_store.CONTEXT_FILE, enriched_ctx)

    events.emit(log_dir, "round_start", round_num=round_num, phase=phase)
    metrics.log_metrics(log_dir, event="round_start", round_num=round_num, phase=phase)

    prompt = _api_assemble_prompt(cfg, phase=phase, context=enriched_ctx)

    events.emit(log_dir, "agent_spawn", round_num=round_num, timeout_s=timeout_s)
    framework_env = {
        "AGENT_RUNNER_LOG_DIR": str(log_dir),
        "AGENT_RUNNER_ROUND_NUM": str(round_num),
        "AGENT_RUNNER_PHASE": phase or "",
    }

    def _progress_emit(stats: dict) -> None:
        api.emit_round_progress(
            log_dir,
            round_num=round_num,
            **stats,
        )

    result = agent_runtime.run(
        command=cfg.agent.command,
        prompt_arg_template=cfg.agent.prompt_arg_template,
        prompt=prompt,
        timeout_s=timeout_s,
        log_path=log_path,
        env_extra={**framework_env, **dict(cfg.agent.env)},
        max_grace_after_result_s=cfg.runtime.max_grace_after_result_s,
        progress_callback=_progress_emit,
        progress_interval_s=cfg.monitor.round_progress_interval_s,
    )
    events.emit(
        log_dir,
        "agent_exit",
        round_num=round_num,
        exit_code=result.exit_code,
        duration_s=result.duration_s,
        timed_out=result.timed_out,
    )

    _scan_round_log_for_network_blip(
        log_dir=log_dir,
        log_path=log_path,
        result=result,
        round_num=round_num,
        phase=phase,
    )

    dirty = vcs_state.detect_dirty_files(cfg.runtime.work_dir)
    if dirty:
        events.emit(log_dir, "dirty_detected", round_num=round_num, files=dirty[:20])

    stashed = False
    action = cfg.vcs.dirty_action
    if dirty and not result.timed_out and result.exit_code == 0:
        if action == "stash":
            ref = vcs_state.stash_orphan(
                cfg.runtime.work_dir,
                round_num=round_num,
                phase=phase,
                idempotency_s=cfg.vcs.stash_idempotency_s,
            )
            if ref is not None:
                context_store.write_orphan_state(
                    log_dir,
                    context_store.OrphanState(
                        round_num=round_num,
                        files=dirty,
                        stashed_ref=ref.sha,
                        stash_message=ref.message,
                        timestamp=now_iso_ms(),
                        phase=phase,
                    ),
                )
                events.emit(
                    log_dir,
                    "orphan_stashed",
                    round_num=round_num,
                    ref=ref.sha,
                    reason="clean_exit_with_dirty_tree",
                )
                stashed = True
        elif action == "ignore":
            # Leave tree dirty for next round; dirty_detected already emitted
            pass
        elif action == "auto_commit":
            err = vcs_state.try_auto_commit(cfg.runtime.work_dir, round_num, phase)
            if err is not None:
                events.emit(
                    log_dir,
                    events.DIRTY_COMMIT_FAILED,
                    round_num=round_num,
                    phase=phase,
                    reason=err,
                )
    elif not dirty:
        context_store.clear_orphan_state(log_dir)

    if result.killed_for_grace:
        api.emit_round_grace_kill(
            log_dir,
            round_num=round_num,
            grace_s=cfg.runtime.max_grace_after_result_s,
        )
    elif result.timed_out:
        events.emit(
            log_dir,
            "round_timeout_kill",
            round_num=round_num,
            reason=f"exceeded round_timeout_s={timeout_s}",
        )

    completed_at = now_iso_ms()
    context_store.write_status(
        log_dir,
        context_store.Status(
            round_num=round_num,
            running=False,
            last_completed_at=completed_at,
            last_exit_code=result.exit_code,
            last_duration_s=result.duration_s,
            current_phase=phase,
            phase_index=phase_idx,
        ),
    )
    metrics.log_metrics(log_dir, event="round_end", round_num=round_num, phase=phase)
    events.emit(log_dir, "round_end", round_num=round_num)

    round_result = RoundResult(
        round_num=round_num,
        phase=phase,
        started_at=started_at,
        ended_at=completed_at,
        exit_code=result.exit_code,
        duration_s=result.duration_s,
        timed_out=result.timed_out,
        log_path=log_path,
        dirty_files=dirty,
        stashed=stashed,
        killed_for_grace=result.killed_for_grace,
    )
    _run_post_round_hooks(hook_ctx, round_result, log_dir)
    return round_result
