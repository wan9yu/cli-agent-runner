"""Event emission wrappers — one round's own lifecycle as the supervisor sees
it (substrate fingerprints, wedge/grace kills, log pruning). Re-exported from
``agent_runner._emit`` (the package facade) — see its docstring.
"""

from __future__ import annotations

from pathlib import Path


def emit_stale_index_lock_cleared(log_dir: Path, *, lock_path: str, round_num: int) -> None:
    """Emit when serve removed a .git/index.lock that its own timed-out+killed git
    call orphaned. Single-writer: serve holds the round lock, so any lock surviving
    our kill is ours to clear."""
    from agent_runner.events import STALE_INDEX_LOCK_CLEARED, emit

    emit(log_dir, STALE_INDEX_LOCK_CLEARED, lock_path=lock_path, round_num=round_num)


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
