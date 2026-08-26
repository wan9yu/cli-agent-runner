"""Runnable reference for docs/plugins.md § ContextEnricher.

Each registered ContextEnricher returns a ``dict`` slice; the runner merges it
into the round's context dict under the enricher's own ``name`` key, so two
enrichers never collide. This test registers two real enrichers and asserts the
exact shape of that MERGED dict as produced by ``runner._stitch_enricher_slices``
-- it verifies the in-memory namespacing contract, not any ``round-context.json``
file on disk.
"""

from __future__ import annotations

from pathlib import Path

from agent_runner import hooks
from agent_runner.runner import _stitch_enricher_slices
from tests._test_helpers import isolating

_reset = isolating(hooks._CONTEXT_ENRICHERS)


class _Branch:
    name = "branch_info"

    def enrich(self, ctx):
        return {"branch": "main"}


class _Stats:
    name = "review_stats"

    def enrich(self, ctx):
        return {"open_prs": 3}


def test_given_two_enrichers_when_stitched_then_both_namespaced(tmp_path: Path) -> None:
    hooks.register_context_enricher(_Branch())
    hooks.register_context_enricher(_Stats())

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    hook_ctx = hooks.HookContext(
        work_dir=tmp_path,
        log_dir=log_dir,
        project="proj",
        round_num=1,
        phase=None,
        agent_name=None,
    )

    base = {"round_num": 1, "started_at": "2026-01-01T00:00:00.000Z"}
    out = _stitch_enricher_slices(base, hooks.context_enrichers(), hook_ctx, log_dir)

    assert out["round_num"] == 1
    assert out["branch_info"] == {"branch": "main"}
    assert out["review_stats"] == {"open_prs": 3}
