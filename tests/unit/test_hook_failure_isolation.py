"""When a hook raises, runner emits ``hook_failed`` event and continues.

Validated at the helper boundary (``_stitch_enricher_slices``) — the same
try/except pattern is used by pre_round and post_round wrappers.
"""

from __future__ import annotations

import json
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


class _Crash:
    name = "boom"

    def enrich(self, ctx):
        raise RuntimeError("simulated plugin crash")


class _Ok:
    name = "fine"

    def enrich(self, ctx):
        return {"ok": True}


def test_given_enricher_raises_when_stitched_then_hook_failed_emitted_and_round_continues(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    hooks.register_context_enricher(_Crash())
    hooks.register_context_enricher(_Ok())

    hook_ctx = hooks.HookContext(
        work_dir=tmp_path,
        log_dir=log_dir,
        project="proj",
        round_num=1,
        phase=None,
        agent_name=None,
    )

    out = _stitch_enricher_slices({"round_num": 1}, hooks.context_enrichers(), hook_ctx, log_dir)

    # Round continued: the good enricher's slice is present
    assert out["fine"] == {"ok": True}
    # The bad enricher's slot is absent (no slice merged on failure)
    assert "boom" not in out
    # hook_failed event was emitted
    event_files = list(log_dir.glob("events-*.jsonl"))
    assert event_files, "no events file written"
    lines = event_files[0].read_text().splitlines()
    matches = [json.loads(line) for line in lines if "hook_failed" in line]
    assert matches, f"no hook_failed event found in {lines}"
    payload = matches[0]
    assert payload["event"] == "hook_failed"
    assert payload["hook_name"] == "boom"
    assert payload["hook_kind"] == "context_enricher"
    assert payload["error_type"] == "RuntimeError"
    assert "simulated plugin crash" in payload["error_message"]
    assert "traceback" in payload
