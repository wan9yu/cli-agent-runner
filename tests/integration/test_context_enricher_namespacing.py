"""Two ContextEnrichers produce two namespaced slices via runner's stitch helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import hooks
from agent_runner.runner import _stitch_enricher_slices


@pytest.fixture(autouse=True)
def _reset_enrichers():
    saved = list(hooks._CONTEXT_ENRICHERS)
    hooks._CONTEXT_ENRICHERS.clear()
    yield
    hooks._CONTEXT_ENRICHERS.clear()
    hooks._CONTEXT_ENRICHERS.extend(saved)


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
