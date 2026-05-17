# tests/unit/test_gemini_plugin.py
"""Unit tests for the GeminiErrorDetector plugin (usage + error classifier)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

_MOD = "agent_runner.builtin_plugins.gemini"


def _make_hook_context(tmp_path: Path, *, agent_name: str = "gemini", round_num: int = 1):
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


def test_given_single_model_gemini_round_when_after_round_then_usage_emitted_with_primary_model(
    tmp_path,
):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "status": "success",
                "stats": {
                    "total_tokens": 19986,
                    "input_tokens": 19733,
                    "output_tokens": 91,
                    "cached": 15119,
                    "input": 4614,
                    "duration_ms": 5337,
                    "tool_calls": 1,
                    "models": {
                        "gemini-3-flash-preview": {
                            "total_tokens": 18934,
                            "input_tokens": 18816,
                            "output_tokens": 51,
                            "cached": 15119,
                            "input": 3697,
                        },
                    },
                },
            }
        ],
    )
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
            GeminiErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    usage_emit.assert_called_once()
    kwargs = usage_emit.call_args.kwargs
    assert kwargs["agent"] == "gemini"
    assert kwargs["model"] == "gemini-3-flash-preview"
    assert kwargs["input_tokens"] == 4614  # 19733 - 15119
    assert kwargs["output_tokens"] == 91
    assert kwargs["cached_tokens"] == 15119
    assert kwargs["cost_usd"] is None
    assert kwargs["duration_ms"] == 5337
    assert kwargs["models_breakdown"] is None  # single-model
    err_emit.assert_not_called()


def test_given_multi_model_gemini_round_when_after_round_then_models_breakdown_populated(
    tmp_path,
):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "status": "success",
                "stats": {
                    "total_tokens": 19986,
                    "input_tokens": 19733,
                    "output_tokens": 91,
                    "cached": 15119,
                    "duration_ms": 5337,
                    "models": {
                        "gemini-3-flash-preview": {
                            "total_tokens": 18934,
                            "input_tokens": 18816,
                            "output_tokens": 51,
                            "cached": 15119,
                        },
                        "gemini-3.1-flash-lite": {
                            "total_tokens": 1052,
                            "input_tokens": 917,
                            "output_tokens": 40,
                            "cached": 0,
                        },
                    },
                },
            }
        ],
    )
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        GeminiErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    kwargs = usage_emit.call_args.kwargs
    assert kwargs["model"] == "gemini-3-flash-preview"  # primary by total_tokens
    assert kwargs["models_breakdown"] is not None
    assert len(kwargs["models_breakdown"]) == 2
    assert "gemini-3-flash-preview" in kwargs["models_breakdown"]
    assert "gemini-3.1-flash-lite" in kwargs["models_breakdown"]


def test_given_non_gemini_preset_when_after_round_then_no_emit(tmp_path):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [{"type": "result", "status": "success", "stats": {"total_tokens": 100}}],
    )
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
            GeminiErrorDetector().after_round(
                _make_hook_context(tmp_path, agent_name="claude"), result=MagicMock()
            )
    usage_emit.assert_not_called()
    err_emit.assert_not_called()


def test_given_gemini_5xx_error_when_after_round_then_transient_error_detected(tmp_path):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "status": "error",
                "error": {"code": 500, "message": "Internal server error"},
                "stats": {
                    "total_tokens": 100,
                    "input_tokens": 100,
                    "output_tokens": 0,
                    "cached": 0,
                    "duration_ms": 562,
                    "models": {},
                },
            }
        ],
    )
    with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
        with patch(f"{_MOD}.emit_agent_usage_recorded"):
            with patch(f"{_MOD}.time.time", return_value=1000):
                GeminiErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    err_emit.assert_called_once()
    kwargs = err_emit.call_args.kwargs
    assert kwargs["classification"] == "api_transient_5xx"
    assert kwargs["agent"] == "gemini"
    assert kwargs["reset_at_epoch"] == 1060  # now + 60s default


def test_given_gemini_429_error_when_after_round_then_classified_as_model_rate_limit(tmp_path):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "status": "error",
                "error": {"code": 429, "message": "Rate limited"},
                "stats": {
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached": 0,
                    "duration_ms": 100,
                    "models": {},
                },
            }
        ],
    )
    with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
        with patch(f"{_MOD}.emit_agent_usage_recorded"):
            with patch(f"{_MOD}.time.time", return_value=1000):
                GeminiErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    err_emit.assert_called_once()
    assert err_emit.call_args.kwargs["classification"] == "rate_limit_model"


def test_given_gemini_unknown_error_code_when_after_round_then_no_transient_error(tmp_path):
    """403/404/etc. not transient — no transient_error_detected emit; usage still emitted."""
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    _write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "status": "error",
                "error": {"code": 403, "message": "Forbidden"},
                "stats": {
                    "total_tokens": 50,
                    "input_tokens": 50,
                    "output_tokens": 0,
                    "cached": 0,
                    "duration_ms": 200,
                    "models": {},
                },
            }
        ],
    )
    with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
        with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
            GeminiErrorDetector().after_round(_make_hook_context(tmp_path), result=MagicMock())
    err_emit.assert_not_called()
    usage_emit.assert_called_once()
