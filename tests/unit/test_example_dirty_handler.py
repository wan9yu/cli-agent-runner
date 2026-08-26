"""Runnable reference for docs/plugins.md § DirtyHandler.

The doc points here instead of carrying an un-run override recipe: this proves
a custom ``DirtyHandler`` — registered alongside the bundled
``DefaultDirtyHandler`` (priority 1000) — actually claims the outcome, because
priority dispatch runs the lower-priority custom handler first and stops on its
non-``None`` ``DirtyOutcome``. The bundled default's stash never runs.
"""

from __future__ import annotations

from agent_runner import hooks
from agent_runner.api_types import DirtyOutcome
from agent_runner.builtin_plugins.default_dirty_handler import DefaultDirtyHandler
from tests._test_helpers import make_hook_context


class CommitEverythingHandler:
    """Minimal custom DirtyHandler: claims every dirty tree as a commit.

    ``priority`` below the bundled default (1000) so dispatch reaches it first;
    returning a ``DirtyOutcome`` stops dispatch before the default can stash.
    """

    name = "example_commit_everything"
    priority = 10  # ascending; lower runs first, so this beats the default (1000)

    def handle_dirty(self, ctx: hooks.HookContext, dirty_files: list[str]) -> DirtyOutcome | None:
        return DirtyOutcome(kind="committed", ref="cafef00d")


def test_given_custom_handler_when_dispatched_then_it_wins_over_default_stash(
    tmp_path, monkeypatch
) -> None:
    # Isolate the registry the way test_dirty_handlers.py does, so this
    # registration never leaks into other tests.
    monkeypatch.setattr(hooks, "_DIRTY_HANDLERS", [])
    hooks.register_dirty_handler(DefaultDirtyHandler())  # priority 1000
    hooks.register_dirty_handler(CommitEverythingHandler())  # priority 10

    ctx = make_hook_context(work_dir=tmp_path, log_dir=tmp_path)
    outcome = hooks.dispatch_dirty(ctx, ["notes.md"], log_dir=tmp_path)

    # Priority dispatch picked the custom handler over the default's stash:
    # the default (1000) never ran, so no stash was created.
    assert outcome == DirtyOutcome(kind="committed", ref="cafef00d")
