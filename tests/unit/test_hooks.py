"""Tests for agent_runner.hooks — Protocols + registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import hooks
from tests._test_helpers import isolating

_reset = isolating(
    hooks._PRE_ROUND_HOOKS,
    hooks._CONTEXT_ENRICHERS,
    hooks._POST_ROUND_HOOKS,
)


def test_given_hook_context_when_constructed_then_carries_round_fields() -> None:
    ctx = hooks.HookContext(
        work_dir=Path("/tmp/proj"),
        log_dir=Path("/tmp/proj/logs"),
        project="proj",
        round_num=42,
        phase="diverge",
        agent_name="some-cli",
    )
    assert ctx.round_num == 42
    assert ctx.phase == "diverge"
    assert ctx.agent_name == "some-cli"


def test_given_no_plugins_when_listed_then_empty() -> None:
    assert hooks.pre_round_hooks() == []
    assert hooks.context_enrichers() == []
    assert hooks.post_round_hooks() == []
    assert hooks.plugin_context_enrichers() == []


def test_given_pre_round_hook_when_registered_then_visible_in_listing() -> None:
    class MyPreRound:
        name = "mine"

        def before_round(self, ctx: hooks.HookContext) -> None:
            return None

    hooks.register_pre_round_hook(MyPreRound())
    listing = hooks.pre_round_hooks()
    assert len(listing) == 1
    assert listing[0].name == "mine"


def test_given_context_enricher_when_registered_then_visible_in_listing() -> None:
    class MyEnricher:
        name = "branch_info"

        def enrich(self, ctx: hooks.HookContext) -> dict:
            return {"branch": "main"}

    hooks.register_context_enricher(MyEnricher())
    assert [e.name for e in hooks.context_enrichers()] == ["branch_info"]
    assert hooks.plugin_context_enrichers() == ["branch_info"]


def test_given_post_round_hook_when_registered_then_visible_in_listing() -> None:
    class MyPostRound:
        name = "logger"

        def after_round(self, ctx: hooks.HookContext, result) -> None:  # noqa: ANN001
            return None

    hooks.register_post_round_hook(MyPostRound())
    listing = hooks.post_round_hooks()
    assert len(listing) == 1


def test_given_duplicate_enricher_name_when_registered_then_raises() -> None:
    class A:
        name = "dup"

        def enrich(self, ctx):
            return {}

    class B:
        name = "dup"

        def enrich(self, ctx):
            return {}

    hooks.register_context_enricher(A())
    with pytest.raises(ValueError, match="already registered"):
        hooks.register_context_enricher(B())


def test_given_duplicate_pre_hook_name_when_registered_then_raises() -> None:
    class A:
        name = "dup_pre"

        def before_round(self, ctx):
            return None

    class B:
        name = "dup_pre"

        def before_round(self, ctx):
            return None

    hooks.register_pre_round_hook(A())
    with pytest.raises(ValueError, match="already registered"):
        hooks.register_pre_round_hook(B())


def test_given_summarize_error_when_called_then_truncates_long_traceback() -> None:
    """Tracebacks > 2KB are truncated head 1KB + tail 1KB with separator."""
    long_tb = "x" * 5000
    out = hooks._summarize_error(RuntimeError("boom"), tb=long_tb)
    assert out["error_type"] == "RuntimeError"
    assert out["error_message"] == "boom"
    assert len(out["traceback"]) <= 2200
    assert out["traceback"].startswith("x" * 100)
    assert "[truncated]" in out["traceback"]


def test_given_short_traceback_when_summarized_then_kept_intact() -> None:
    short_tb = "short trace"
    out = hooks._summarize_error(ValueError("x"), tb=short_tb)
    assert out["traceback"] == "short trace"
    assert "[truncated]" not in out["traceback"]
