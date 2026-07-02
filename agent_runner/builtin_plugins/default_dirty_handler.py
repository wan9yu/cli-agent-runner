"""Bundled default dirty-tree handler — reproduces the pre-0.2.0 built-in
behavior (stash / ignore / auto_commit per [vcs] dirty_action) as a plugin.
Ships enabled; disable via [plugins] disable = ["default_dirty_handler"]."""

from __future__ import annotations

from agent_runner import api, context_store, events
from agent_runner.api_types import DirtyOutcome
from agent_runner.events import now_iso_ms  # match the helper runner.py uses
from agent_runner.hooks import HookContext, register_dirty_handler
from agent_runner.vcs_state import AutoCommitError


class DefaultDirtyHandler:
    name = "default_dirty_handler"
    priority = 1000  # last-resort fallback

    def handle_dirty(self, ctx: HookContext, dirty_files, _result) -> DirtyOutcome | None:
        action = ctx.vcs.dirty_action if ctx.vcs else "stash"
        if action == "ignore":
            return DirtyOutcome(kind="ignored")
        if action == "auto_commit":
            try:
                sha = api.try_auto_commit(
                    ctx.work_dir, ctx.round_num, ctx.phase, log_dir=ctx.log_dir
                )
            except AutoCommitError as exc:
                # Parity with runner.py: emit failure event, leave tree dirty
                # (no stash fallback). Dirty tree carries into the next round.
                events.emit(
                    ctx.log_dir,
                    events.DIRTY_COMMIT_FAILED,
                    round_num=ctx.round_num,
                    phase=ctx.phase,
                    reason=str(exc),
                )
                return DirtyOutcome(kind="ignored")
            if not sha:
                return DirtyOutcome(kind="ignored")
            events.emit(
                ctx.log_dir,
                events.DIRTY_AUTO_COMMITTED,
                round_num=ctx.round_num,
                files=dirty_files[:20],
                ref=sha,
            )
            return DirtyOutcome(kind="committed", ref=sha)
        return self._stash(ctx, dirty_files)

    def _stash(self, ctx: HookContext, dirty_files) -> DirtyOutcome:
        ref = api.stash_orphan(
            ctx.work_dir,
            round_num=ctx.round_num,
            phase=ctx.phase,
            idempotency_s=ctx.vcs.stash_idempotency_s if ctx.vcs else 5,
            log_dir=ctx.log_dir,
        )
        if ref is None:
            return DirtyOutcome(kind="ignored")
        context_store.write_orphan_state(
            ctx.log_dir,
            context_store.OrphanState(
                round_num=ctx.round_num,
                files=dirty_files,
                stashed_ref=ref.sha,
                stash_message=ref.message,
                timestamp=now_iso_ms(),
                phase=ctx.phase,
            ),
        )
        events.emit(
            ctx.log_dir,
            "orphan_stashed",
            round_num=ctx.round_num,
            ref=ref.sha,
            reason="clean_exit_with_dirty_tree",
        )
        return DirtyOutcome(kind="stashed", ref=ref.sha)


register_dirty_handler(DefaultDirtyHandler())
