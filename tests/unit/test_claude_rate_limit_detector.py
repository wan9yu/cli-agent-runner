"""Unit tests for the built-in claude_rate_limit_detector post_round_hook."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_hook_context(tmp_path: Path, *, agent_name: str = "claude", round_num: int = 1):
    """Build a minimal HookContext for testing the detector."""
    from agent_runner.hooks import HookContext

    return HookContext(
        work_dir=tmp_path,
        log_dir=tmp_path,
        project="testproj",
        round_num=round_num,
        phase=None,
        agent_name=agent_name,
    )


def _write_round_log(log_dir: Path, round_num: int, lines: list[str]) -> Path:
    log_path = log_dir / f"round-{round_num}.log"
    log_path.write_text("\n".join(lines) + "\n")
    return log_path


def test_given_rate_limit_event_in_log_when_after_round_then_emits_rejected(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeRateLimitDetector

    ctx = _make_hook_context(tmp_path)
    rate_limit_line = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": 1778903400,
                "rateLimitType": "five_hour",
            },
        }
    )
    result_line = json.dumps(
        {
            "type": "result",
            "is_error": True,
            "api_error_status": 429,
            "result": "You've hit your limit · resets 11:50pm",
        }
    )
    _write_round_log(tmp_path, 1, [rate_limit_line, result_line])
    with patch("agent_runner.builtin_plugins.claude_rate_limit.emit") as mock_emit:
        ClaudeRateLimitDetector().after_round(ctx, result=MagicMock())
    mock_emit.assert_called_once()
    args, kwargs = mock_emit.call_args
    assert args[0] == tmp_path  # log_dir
    assert args[1] == "rate_limit_rejected"
    assert kwargs["agent"] == "claude"
    assert kwargs["reset_at_epoch"] == 1778903400
    assert kwargs["limit_type"] == "five_hour"
    assert kwargs["round_num"] == 1
    assert "You've hit your limit" in kwargs["raw"]


def test_given_429_result_without_rate_limit_event_when_after_round_then_emits_with_fallback(
    tmp_path,
):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeRateLimitDetector

    ctx = _make_hook_context(tmp_path)
    result_line = json.dumps(
        {
            "type": "result",
            "is_error": True,
            "api_error_status": 429,
            "result": "rate limited",
        }
    )
    _write_round_log(tmp_path, 1, [result_line])
    with patch("agent_runner.builtin_plugins.claude_rate_limit.emit") as mock_emit:
        with patch("agent_runner.builtin_plugins.claude_rate_limit.time.time", return_value=1000):
            ClaudeRateLimitDetector().after_round(ctx, result=MagicMock())
    mock_emit.assert_called_once()
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["reset_at_epoch"] == 1300  # now + 300 fallback
    assert kwargs["limit_type"] == "unknown"


def test_given_no_rate_limit_in_log_when_after_round_then_no_emit(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeRateLimitDetector

    ctx = _make_hook_context(tmp_path)
    success_line = json.dumps({"type": "result", "is_error": False, "result": "done"})
    _write_round_log(tmp_path, 1, [success_line])
    with patch("agent_runner.builtin_plugins.claude_rate_limit.emit") as mock_emit:
        ClaudeRateLimitDetector().after_round(ctx, result=MagicMock())
    mock_emit.assert_not_called()


def test_given_non_claude_preset_when_after_round_then_returns_early(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeRateLimitDetector

    ctx = _make_hook_context(tmp_path, agent_name="aider")
    rate_limit_line = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": 1778903400,
                "rateLimitType": "five_hour",
            },
        }
    )
    _write_round_log(tmp_path, 1, [rate_limit_line])
    with patch("agent_runner.builtin_plugins.claude_rate_limit.emit") as mock_emit:
        ClaudeRateLimitDetector().after_round(ctx, result=MagicMock())
    mock_emit.assert_not_called()


def test_given_malformed_jsonl_when_after_round_then_skips_invalid_and_continues(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeRateLimitDetector

    ctx = _make_hook_context(tmp_path)
    lines = [
        "this is not json",
        json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected",
                    "resetsAt": 1778903400,
                    "rateLimitType": "five_hour",
                },
            }
        ),
        "{partial json",
    ]
    _write_round_log(tmp_path, 1, lines)
    with patch("agent_runner.builtin_plugins.claude_rate_limit.emit") as mock_emit:
        ClaudeRateLimitDetector().after_round(ctx, result=MagicMock())
    mock_emit.assert_called_once()
    assert mock_emit.call_args.kwargs["reset_at_epoch"] == 1778903400


def test_given_missing_log_file_when_after_round_then_no_crash_no_emit(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeRateLimitDetector

    ctx = _make_hook_context(tmp_path, round_num=99)  # round-99.log does not exist
    with patch("agent_runner.builtin_plugins.claude_rate_limit.emit") as mock_emit:
        ClaudeRateLimitDetector().after_round(ctx, result=MagicMock())
    mock_emit.assert_not_called()
