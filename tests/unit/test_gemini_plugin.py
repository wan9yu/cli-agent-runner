# tests/unit/test_gemini_plugin.py
"""Unit tests for the GeminiErrorDetector plugin (usage + error classifier)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests._test_helpers import make_hook_context, write_round_log

_MOD = "agent_runner.builtin_plugins.gemini"


def test_given_single_model_gemini_round_when_after_round_then_usage_emitted_with_primary_model(
    tmp_path,
):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    write_round_log(
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
            GeminiErrorDetector().after_round(
                make_hook_context(tmp_path, agent_name="gemini"), result=MagicMock()
            )
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
    assert kwargs["tool_call_count"] == 1
    assert kwargs["cache_creation_tokens"] == 0  # gemini has no creation concept
    err_emit.assert_not_called()


def test_given_multi_model_gemini_round_when_after_round_then_models_breakdown_populated(
    tmp_path,
):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    write_round_log(
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
                    "input": 4614,  # net: input_tokens - cached
                    "duration_ms": 5337,
                    "models": {
                        "gemini-3-flash-preview": {
                            "total_tokens": 18934,
                            "input_tokens": 18816,
                            "output_tokens": 51,
                            "cached": 15119,
                            "input": 3697,
                        },
                        "gemini-3.1-flash-lite": {
                            "total_tokens": 1052,
                            "input_tokens": 917,
                            "output_tokens": 40,
                            "cached": 0,
                            "input": 917,
                        },
                    },
                },
            }
        ],
    )
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        GeminiErrorDetector().after_round(
            make_hook_context(tmp_path, agent_name="gemini"), result=MagicMock()
        )
    kwargs = usage_emit.call_args.kwargs
    assert kwargs["model"] == "gemini-3-flash-preview"  # primary by total_tokens
    assert kwargs["models_breakdown"] is not None
    assert len(kwargs["models_breakdown"]) == 2
    assert "gemini-3-flash-preview" in kwargs["models_breakdown"]
    assert "gemini-3.1-flash-lite" in kwargs["models_breakdown"]


def test_given_non_gemini_preset_when_after_round_then_no_emit(tmp_path):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    write_round_log(
        tmp_path,
        1,
        [{"type": "result", "status": "success", "stats": {"total_tokens": 100}}],
    )
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
            GeminiErrorDetector().after_round(
                make_hook_context(tmp_path, agent_name="claude"), result=MagicMock()
            )
    usage_emit.assert_not_called()
    err_emit.assert_not_called()


def test_given_gemini_5xx_error_when_after_round_then_transient_error_detected(tmp_path):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    write_round_log(
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
                GeminiErrorDetector().after_round(
                    make_hook_context(tmp_path, agent_name="gemini"), result=MagicMock()
                )
    err_emit.assert_called_once()
    kwargs = err_emit.call_args.kwargs
    assert kwargs["classification"] == "api_transient_5xx"
    assert kwargs["agent"] == "gemini"
    assert kwargs["reset_at_epoch"] == 1060  # now + 60s default


def test_given_gemini_429_error_when_after_round_then_classified_as_model_rate_limit(tmp_path):
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    write_round_log(
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
                GeminiErrorDetector().after_round(
                    make_hook_context(tmp_path, agent_name="gemini"), result=MagicMock()
                )
    err_emit.assert_called_once()
    assert err_emit.call_args.kwargs["classification"] == "rate_limit_model"


def test_given_gemini_unknown_error_code_when_after_round_then_no_transient_error(tmp_path):
    """403/404/etc. not transient — no transient_error_detected emit; usage still emitted."""
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    write_round_log(
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
            GeminiErrorDetector().after_round(
                make_hook_context(tmp_path, agent_name="gemini"), result=MagicMock()
            )
    err_emit.assert_not_called()
    usage_emit.assert_called_once()


def test_given_gemini_multi_model_when_extracted_then_breakdown_entries_use_canonical_keys(
    tmp_path,
):
    """models_breakdown per-model entries must not duplicate raw input/cached keys.

    Pre-0.1.28 gemini stats.input + stats.cached were passed through to breakdown
    entries alongside canonical input_tokens / cached_tokens (duplicates of same value).
    """
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "status": "success",
                "stats": {
                    "total_tokens": 100,
                    "input_tokens": 80,
                    "output_tokens": 10,
                    "cached": 5,
                    "input": 75,
                    "duration_ms": 100,
                    "tool_calls": 0,
                    "models": {
                        "gemini-3-flash-preview": {
                            "total_tokens": 100,
                            "input_tokens": 80,
                            "output_tokens": 10,
                            "cached": 5,
                            "input": 75,
                        },
                        "gemini-3.1-flash-lite": {
                            "total_tokens": 50,
                            "input_tokens": 40,
                            "output_tokens": 10,
                            "cached": 0,
                            "input": 40,
                        },
                    },
                },
            }
        ],
    )
    ctx = make_hook_context(tmp_path, agent_name="gemini")
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        GeminiErrorDetector().after_round(ctx, result=MagicMock(exit_code=0, timed_out=False))
    breakdown = usage_emit.call_args.kwargs["models_breakdown"]
    entry = breakdown["gemini-3-flash-preview"]
    assert "input" not in entry, f"raw 'input' key leaked: {entry}"
    assert "cached" not in entry, f"raw 'cached' key leaked: {entry}"
    assert entry["input_tokens"] == 80
    assert entry["cached_tokens"] == 5
    assert entry["output_tokens"] == 10
    assert entry["total_tokens"] == 100


def test_given_gemini_round_with_phase_and_success_when_after_round_then_fields_emitted(
    tmp_path,
):
    """phase and success plumbed through from HookContext / RoundResult for gemini."""
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "status": "success",
                "stats": {
                    "total_tokens": 100,
                    "input_tokens": 80,
                    "output_tokens": 10,
                    "cached": 5,
                    "input": 75,
                    "duration_ms": 100,
                    "tool_calls": 0,
                    "models": {},
                },
            }
        ],
    )
    ctx = make_hook_context(tmp_path, agent_name="gemini", phase="planning")
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        GeminiErrorDetector().after_round(ctx, result=MagicMock(exit_code=0, timed_out=False))
    kwargs = usage_emit.call_args.kwargs
    assert kwargs["phase"] == "planning"
    assert kwargs["success"] is True


def test_given_custom_agent_name_with_gemini_binary_when_after_round_then_event_emitted(tmp_path):
    """Regression: same 0.1.29 bug class for gemini detector — custom agent name
    suppresses events when guard uses agent_name instead of agent_binary.
    """
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    round_log = log_dir / "round-1.log"
    round_log.write_text(
        '{"type":"result","status":"success","stats":{"total_tokens":100,"input_tokens":80,'
        '"output_tokens":10,"cached":5,"input":75,"duration_ms":100,"tool_calls":0,"models":{}}}\n',
        encoding="utf-8",
    )
    ctx = make_hook_context(
        tmp_path,
        agent_name="argus_qa",
        agent_binary="gemini",
        agent_log_path=round_log,
    )
    result = MagicMock(exit_code=0, timed_out=False)
    with patch(f"{_MOD}.emit_agent_usage_recorded") as emit:
        GeminiErrorDetector().after_round(ctx, result)
    emit.assert_called_once()


def test_given_non_gemini_binary_when_after_round_then_no_event(tmp_path):
    """GeminiErrorDetector must NOT fire for non-gemini binaries."""
    from agent_runner.builtin_plugins.gemini import GeminiErrorDetector

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    ctx = make_hook_context(
        tmp_path,
        agent_name="claude",
        agent_binary="claude",
        agent_log_path=log_dir / "x.log",
    )
    result = MagicMock(exit_code=0, timed_out=False)
    with patch(f"{_MOD}.emit_agent_usage_recorded") as emit:
        GeminiErrorDetector().after_round(ctx, result)
    emit.assert_not_called()
