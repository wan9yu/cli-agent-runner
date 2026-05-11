"""Main round orchestration. Conducts the other modules; does not touch
subprocess / git / prompt details directly. Pure rotation — no event-driven
branches based on prior round state (§7 IMMUTABLE).
"""

from __future__ import annotations

import fcntl
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runner import (
    agent_runtime,
    context_store,
    events,
    metrics,
    prompt_loader,
    startup_check,
    vcs_state,
)
from agent_runner.config import Config
from agent_runner.events import now_iso_ms


class LockHeldError(RuntimeError):
    pass


def _acquire_lock_or_raise(lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        os.close(fd)
        raise LockHeldError(f"another agent-runner is holding {lock_path}") from e
    return fd


@dataclass(frozen=True)
class RoundResult:
    round_num: int
    exit_code: int
    duration_s: float
    timed_out: bool
    dirty_files: list[str]
    stashed: bool


def _phase_for(round_num: int, phases: list[str] | None) -> tuple[str | None, int]:
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


def run_one_round(cfg: Config) -> RoundResult:
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
    lock_fd = _acquire_lock_or_raise(log_dir / "agent-runner.lock")
    try:
        return _run_one_round_inner(cfg)
    finally:
        os.close(lock_fd)


def _run_one_round_inner(cfg: Config) -> RoundResult:
    log_dir = cfg.runtime.log_dir

    prev_status = context_store.read_status(log_dir)
    if (log_dir / "status.json").exists() and prev_status is None:
        events.emit(log_dir, "status_recovered", reason="status.json could not be parsed")

    round_num = (prev_status.round_num if prev_status else 0) + 1
    phase, phase_idx = _phase_for(round_num, cfg.phases)
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

    context_store.write_round_context(
        log_dir,
        round_num=round_num,
        started_at=started_at,
        phase=phase,
        previous=previous_block,
        orphan_stash=orphan_block,
    )
    events.emit(log_dir, "round_start", round_num=round_num, phase=phase)
    metrics.log_metrics(log_dir, event="round_start", round_num=round_num, phase=phase)

    rounds_dir = log_dir / "rounds"
    rounds_dir.mkdir(exist_ok=True)
    log_path = rounds_dir / f"R{round_num}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}.log"

    prompt = prompt_loader.assemble_prompt(
        cfg.prompt.file,
        context=_round_context_for_prompt(round_num, started_at, phase, orphan_block),
        inject_context=cfg.prompt.inject_context,
    )

    events.emit(log_dir, "agent_spawn", round_num=round_num, timeout_s=cfg.runtime.round_timeout_s)
    result = agent_runtime.run(
        command=cfg.agent.command,
        prompt_arg_template=cfg.agent.prompt_arg_template,
        prompt=prompt,
        timeout_s=cfg.runtime.round_timeout_s,
        log_path=log_path,
        env_extra=agent_runtime.merge_critical_envs({}),
    )
    events.emit(
        log_dir,
        "agent_exit",
        round_num=round_num,
        exit_code=result.exit_code,
        duration_s=result.duration_s,
        timed_out=result.timed_out,
    )

    dirty = vcs_state.detect_dirty_files(cfg.runtime.work_dir)
    if dirty:
        events.emit(log_dir, "dirty_detected", round_num=round_num, files=dirty[:20])

    stashed = False
    if dirty and not result.timed_out and result.exit_code == 0:
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
    elif not dirty:
        context_store.clear_orphan_state(log_dir)

    if result.timed_out:
        events.emit(
            log_dir,
            "round_timeout_kill",
            round_num=round_num,
            reason=f"exceeded round_timeout_s={cfg.runtime.round_timeout_s}",
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

    return RoundResult(
        round_num=round_num,
        exit_code=result.exit_code,
        duration_s=result.duration_s,
        timed_out=result.timed_out,
        dirty_files=dirty,
        stashed=stashed,
    )
