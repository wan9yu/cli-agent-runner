"""Upstream-CLI schema canary.

Captures real-CLI JSONL output as fixtures. Invariant: plugin parsers must
produce the expected schema when fed these fixtures. Catches Anthropic/Google
CLI schema drift at PR-test time instead of in production smoke.

Maintenance: when intentionally upgrading detection to a new CLI version,
(a) capture fresh fixtures from ``agent-runner serve --max-rounds 1`` output,
(b) delete the old fixtures, (c) update the assertions if schema changed.
Failing test = either regression OR intentional upgrade; committer decides.
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cli-real-output"


def test_claude_result_event_parses_to_expected_usage_payload(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import _parse_claude_log

    src = FIXTURES / "claude-2.1.143-result-event.jsonl"
    dest = tmp_path / "round.log"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    parsed = _parse_claude_log(dest)
    assert "usage" in parsed, "claude result event must extract usage payload"
    u = parsed["usage"]
    expected_keys = {
        "agent",
        "model",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "cache_creation_tokens",
        "cost_usd",
        "duration_ms",
        "models_breakdown",
        "tool_call_count",
    }
    assert expected_keys <= set(u.keys()), (
        f"missing keys: {expected_keys - set(u.keys())} "
        f"(fixture may need refresh OR plugin parser regressed)"
    )
    assert u["agent"] == "claude"
    assert u["input_tokens"] == 5
    assert u["cache_creation_tokens"] == 12234
    assert u["cached_tokens"] == 17806
    assert u["cost_usd"] == 0.0855405


def test_claude_assistant_tool_use_counts_correctly(tmp_path):
    """Two assistant events with one tool_use each → tool_call_count == 2.

    Combines the assistant-tool-use fixture with a minimal result event
    to satisfy the parser's result-event requirement.
    """
    from agent_runner.builtin_plugins.claude_rate_limit import _parse_claude_log

    src_assistant = FIXTURES / "claude-2.1.143-assistant-tool-use.jsonl"
    dest = tmp_path / "round.log"
    dest.write_text(
        src_assistant.read_text(encoding="utf-8")
        + '{"type":"result","is_error":false,"usage":{"input_tokens":1,"output_tokens":1,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
        '"duration_ms":100,"total_cost_usd":0.001}\n',
        encoding="utf-8",
    )
    parsed = _parse_claude_log(dest)
    assert parsed["usage"]["tool_call_count"] == 2


def test_gemini_result_event_parses_to_expected_usage_payload(tmp_path):
    from agent_runner.builtin_plugins.gemini import _parse_gemini_log

    src = FIXTURES / "gemini-0.42.0-result-event.jsonl"
    dest = tmp_path / "round.log"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    parsed = _parse_gemini_log(dest)
    assert "usage" in parsed
    u = parsed["usage"]
    assert u["agent"] == "gemini"
    assert u["input_tokens"] == 7339
    assert u["cached_tokens"] == 3796
    assert u["tool_call_count"] == 0
    assert "models_breakdown" in u
    # Canonical keys only in per-model entries (0.1.28 cleanup)
    for model_entry in u["models_breakdown"].values():
        assert "input" not in model_entry, f"raw 'input' key leaked: {model_entry}"
        assert "cached" not in model_entry, f"raw 'cached' key leaked: {model_entry}"
        assert "input_tokens" in model_entry
        assert "cached_tokens" in model_entry
