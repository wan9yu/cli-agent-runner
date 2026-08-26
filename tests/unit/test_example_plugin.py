"""Runnable reference for docs/plugins.md's post_round_hook example.

The doc points here instead of carrying an un-run "template" snippet: this
proves the minimal "parse the round log after each round" plugin actually
compiles and that its ``after_round`` hook fires with a real ``HookContext``.
"""

from __future__ import annotations

import json
from typing import Any

from agent_runner import hooks
from tests._test_helpers import (
    isolating,
    make_hook_context,
    make_run_result,
    write_round_log,
)

# Snapshot/clear/restore the registry so this registration never leaks.
_reset = isolating(hooks._POST_ROUND_HOOKS)


class RoundLogCounter:
    """Minimal PostRoundHook: after each round, parse the agent's merged round
    log (``ctx.agent_log_path``) and record how many JSONL records it holds.

    The per-line ``json.loads`` in try/except is the contract: the log is
    merged stdout+stderr, so non-JSON lines are routine and skipped.
    """

    name = "example_round_log_counter"

    def __init__(self) -> None:
        self.last_count: int | None = None

    def after_round(self, ctx: hooks.HookContext, result: Any) -> None:
        log_path = ctx.agent_log_path
        if log_path is None or not log_path.exists():
            return
        count = 0
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                json.loads(line)
            except ValueError:
                continue  # merged stdout+stderr — skip non-JSON lines
            count += 1
        self.last_count = count


def test_given_registered_example_hook_when_round_ends_then_it_parses_the_log(
    tmp_path,
) -> None:
    hook = RoundLogCounter()
    hooks.register_post_round_hook(hook)
    assert [h.name for h in hooks.post_round_hooks()] == ["example_round_log_counter"]

    write_round_log(tmp_path, 1, [{"type": "a"}, {"type": "b"}, {"type": "c"}])
    ctx = make_hook_context(tmp_path, round_num=1)

    for h in hooks.post_round_hooks():
        h.after_round(ctx, make_run_result(exit_code=0))

    assert hook.last_count == 3
