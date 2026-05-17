# tests/unit/test_claude_error_detector.py
"""Unit tests for the ClaudeErrorDetector (4-bucket classifier + dual-emit)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_hook_context(tmp_path: Path, *, agent_name: str = "claude", round_num: int = 1):
    from agent_runner.hooks import HookContext

    return HookContext(
        work_dir=tmp_path,
        log_dir=tmp_path,
        project="testproj",
        round_num=round_num,
        phase=None,
        agent_name=agent_name,
    )


def _write_round_log(log_dir: Path, round_num: int, events: list[dict]) -> Path:
    log_path = log_dir / f"round-{round_num}.log"
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return log_path


def test_given_rate_limit_event_when_classified_then_rate_limit_account(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    _write_round_log(
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
            ClaudeErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    # New event emitted with rate_limit_account classification
    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "rate_limit_account"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1778903400
    # Old event ALSO emitted (back-compat for rate_limit_account only)
    old_emit.assert_called_once()


def test_given_5xx_error_when_classified_then_api_transient_5xx(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    _write_round_log(
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
                ClaudeErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "api_transient_5xx"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1060  # now + 60s default
    # Old event NOT emitted for non-rate_limit_account
    old_emit.assert_not_called()


def test_given_502_error_then_classified_as_api_transient_5xx(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 502, "result": "bad gateway"}],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    assert new_emit.call_args.kwargs["classification"] == "api_transient_5xx"


def test_given_429_without_rate_limit_event_then_classified_as_rate_limit_model(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    _write_round_log(
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
            ClaudeErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    assert new_emit.call_args.kwargs["classification"] == "rate_limit_model"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1060  # now + 60s


def test_given_408_timeout_then_classified_as_api_timeout(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 408, "result": "timeout"}],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        with patch("agent_runner.builtin_plugins.claude_rate_limit.time.time", return_value=1000):
            ClaudeErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    assert new_emit.call_args.kwargs["classification"] == "api_timeout"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1030  # now + 30s


def test_given_no_error_when_classified_then_no_emit(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    _write_round_log(tmp_path, 1, [{"type": "result", "is_error": False, "result": "done"}])
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    new_emit.assert_not_called()


def test_given_non_claude_preset_when_classified_then_no_emit(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 500}],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(
            _make_hook_context(tmp_path, agent_name="aider"), result=MagicMock()
        )
    new_emit.assert_not_called()


def test_given_unknown_error_status_when_classified_then_no_emit(tmp_path):
    """403, 404, etc. — not transient; classifier returns None."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 403, "result": "forbidden"}],
    )
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    new_emit.assert_not_called()


def test_given_malformed_jsonl_when_classified_then_skips_invalid_and_continues(tmp_path):
    """Malformed JSONL line (not parseable) is silently skipped; valid lines still classified."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    log_path = tmp_path / "round-1.log"
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
            ClaudeErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
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
            _make_hook_context(tmp_path, round_num=99), result=MagicMock()
        )
    new_emit.assert_not_called()
