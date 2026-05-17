# tests/unit/test_claude_error_detector.py
"""Unit tests for the ClaudeErrorDetector (4-bucket classifier + dual-emit)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tests._test_helpers import make_hook_context, write_round_log


def test_given_rate_limit_event_when_classified_then_rate_limit_account(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected",
                    "resetsAt": 1778903400,
                    "rateLimitType": "five_hour",
                },
            },
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 429,
                "result": "limit hit",
            },
        ],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        with patch(
            "agent_runner.builtin_plugins.claude_rate_limit.emit_rate_limit_rejected"
        ) as old_emit:
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    # New event emitted with rate_limit_account classification
    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "rate_limit_account"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1778903400
    # Old event ALSO emitted (back-compat for rate_limit_account only)
    old_emit.assert_called_once()


def test_given_5xx_error_when_classified_then_api_transient_5xx(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 500,
                "result": "API Error: 500 Internal server error",
            },
        ],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        with patch(
            "agent_runner.builtin_plugins.claude_rate_limit.emit_rate_limit_rejected"
        ) as old_emit:
            with patch(
                "agent_runner.builtin_plugins.claude_rate_limit.time.time", return_value=1000
            ):
                ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "api_transient_5xx"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1060  # now + 60s default
    # Old event NOT emitted for non-rate_limit_account
    old_emit.assert_not_called()


def test_given_502_error_then_classified_as_api_transient_5xx(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 502, "result": "bad gateway"}],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    assert new_emit.call_args.kwargs["classification"] == "api_transient_5xx"


def test_given_429_without_rate_limit_event_then_classified_as_rate_limit_model(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 429,
                "result": "model overloaded",
            }
        ],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        with patch("agent_runner.builtin_plugins.claude_rate_limit.time.time", return_value=1000):
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    assert new_emit.call_args.kwargs["classification"] == "rate_limit_model"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1060  # now + 60s


def test_given_408_timeout_then_classified_as_api_timeout(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 408, "result": "timeout"}],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        with patch("agent_runner.builtin_plugins.claude_rate_limit.time.time", return_value=1000):
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    assert new_emit.call_args.kwargs["classification"] == "api_timeout"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1030  # now + 30s


def test_given_no_error_when_classified_then_no_emit(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(tmp_path, 1, [{"type": "result", "is_error": False, "result": "done"}])
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    new_emit.assert_not_called()


def test_given_non_claude_preset_when_classified_then_no_emit(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 500}],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(
            make_hook_context(tmp_path, agent_name="aider"), result=MagicMock()
        )
    new_emit.assert_not_called()


def test_given_unknown_error_status_when_classified_then_no_emit(tmp_path):
    """403, 404, etc. — not transient; classifier returns None."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 403, "result": "forbidden"}],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    new_emit.assert_not_called()


def test_given_malformed_jsonl_when_classified_then_skips_invalid_and_continues(tmp_path):
    """Malformed JSONL line (not parseable) is silently skipped; valid lines still classified."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    rounds_dir = tmp_path / "rounds"
    rounds_dir.mkdir(exist_ok=True)
    log_path = rounds_dir / "R1-test.log"
    log_path.write_text(
        "this is not json\n"
        + json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected",
                    "resetsAt": 1778903400,
                    "rateLimitType": "five_hour",
                },
            }
        )
        + "\n"
        + "{partial json\n"
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        with patch("agent_runner.builtin_plugins.claude_rate_limit.emit_rate_limit_rejected"):
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "rate_limit_account"


def test_given_missing_log_file_when_classified_then_no_crash_no_emit(tmp_path):
    """Missing round-N.log file: no crash, no emit (defensive guard)."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    # round-99.log does not exist in tmp_path
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(
            make_hook_context(tmp_path, round_num=99), result=MagicMock()
        )
    new_emit.assert_not_called()


_MOD = "agent_runner.builtin_plugins.claude_rate_limit"


def test_given_successful_round_with_usage_when_after_round_then_emits_usage(tmp_path):
    """Successful round emits agent_usage_recorded with correct input_tokens (NET per Anthropic)."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {"type": "assistant", "message": {"model": "claude-opus-4-7"}},  # NEW 0.1.26
            {
                "type": "result",
                "is_error": False,
                "subtype": "success",
                "result": "done",
                "total_cost_usd": 0.0812,
                "usage": {
                    "input_tokens": 100,  # Anthropic schema: already NET
                    "output_tokens": 6,
                    "cache_read_input_tokens": 80,
                },
                "duration_ms": 14470,
            },
        ],
    )
    with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
        with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    err_emit.assert_not_called()
    usage_emit.assert_called_once()
    kwargs = usage_emit.call_args.kwargs
    assert kwargs["agent"] == "claude"
    assert kwargs["model"] == "claude-opus-4-7"  # was "unknown" pre-0.1.26
    assert kwargs["input_tokens"] == 100  # was 20 (broken NET formula 100-80) pre-0.1.26
    assert kwargs["output_tokens"] == 6
    assert kwargs["cached_tokens"] == 80
    assert kwargs["cost_usd"] == 0.0812
    assert kwargs["duration_ms"] == 14470
    assert kwargs["models_breakdown"] is None


def test_given_5xx_error_with_usage_when_after_round_then_emits_both_events(tmp_path):
    """Failed round emits BOTH transient_error_detected AND agent_usage_recorded."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {"type": "assistant", "message": {"model": "claude-opus-4-7"}},  # NEW 0.1.26
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 500,
                "result": "API Error: 500",
                "total_cost_usd": 0.001,
                "usage": {"input_tokens": 10, "output_tokens": 0, "cache_read_input_tokens": 0},
                "duration_ms": 562,
            },
        ],
    )
    with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
        with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
            with patch(f"{_MOD}.time.time", return_value=1000):
                ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    err_emit.assert_called_once()
    assert err_emit.call_args.kwargs["classification"] == "api_transient_5xx"
    usage_emit.assert_called_once()
    assert usage_emit.call_args.kwargs["agent"] == "claude"
    assert usage_emit.call_args.kwargs["model"] == "claude-opus-4-7"  # NEW 0.1.26
    assert usage_emit.call_args.kwargs["input_tokens"] == 10  # NET; was 0 pre-0.1.26


def test_given_claude_log_with_assistant_event_when_extracted_then_model_populated(tmp_path):
    """Plugin tracks latest assistant event's message.model (was 'unknown' pre-0.1.26)."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-4-7",
                    "content": [{"type": "text", "text": "hi"}],
                },
            },
            {
                "type": "result",
                "is_error": False,
                "subtype": "success",
                "result": "done",
                "total_cost_usd": 0.05,
                "usage": {"input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 0},
                "duration_ms": 1234,
            },
        ],
    )
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    usage_emit.assert_called_once()
    assert usage_emit.call_args.kwargs["model"] == "claude-opus-4-7"


def test_given_claude_log_without_assistant_event_when_extracted_then_model_unknown(tmp_path):
    """No assistant event (e.g. immediate error before model response) -> model='unknown'."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 500,
                "result": "err",
                "total_cost_usd": 0.0,
                "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0},
                "duration_ms": 50,
            },
        ],
    )
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected"):
            with patch(f"{_MOD}.time.time", return_value=1000):
                ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=MagicMock())
    assert usage_emit.call_args.kwargs["model"] == "unknown"
