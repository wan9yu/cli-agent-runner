# tests/unit/test_claude_error_detector.py
"""Unit tests for the ClaudeErrorDetector (4-bucket classifier + dual-emit)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tests._test_helpers import make_hook_context, make_run_result, write_round_log


def test_rate_limit_event_should_classify_as_rate_limit_account(tmp_path):
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
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "rate_limit_account"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1778903400


@pytest.mark.parametrize("api_error_status", [500, 502], ids=["500", "502"])
def test_5xx_error_should_classify_as_api_transient_5xx(tmp_path, api_error_status):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "is_error": True,
                "api_error_status": api_error_status,
                "result": f"API Error: {api_error_status}",
            },
        ],
    )

    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        with patch("agent_runner.clock.SYSTEM_CLOCK.epoch", return_value=1000):
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "api_transient_5xx"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1060  # now + 60s default


def test_429_error_should_classify_as_rate_limit_model_when_no_rate_limit_event(tmp_path):
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
        with patch("agent_runner.clock.SYSTEM_CLOCK.epoch", return_value=1000):
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    assert new_emit.call_args.kwargs["classification"] == "rate_limit_model"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1060  # now + 60s


def test_408_status_should_classify_as_api_timeout(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [{"type": "result", "is_error": True, "api_error_status": 408, "result": "timeout"}],
    )

    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        with patch("agent_runner.clock.SYSTEM_CLOCK.epoch", return_value=1000):
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    assert new_emit.call_args.kwargs["classification"] == "api_timeout"
    assert new_emit.call_args.kwargs["reset_at_epoch"] == 1030  # now + 30s


def test_successful_round_should_not_emit_transient_error(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(tmp_path, 1, [{"type": "result", "is_error": False, "result": "done"}])

    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    new_emit.assert_not_called()


def test_non_claude_preset_should_not_emit_transient_error(tmp_path):
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
            make_hook_context(tmp_path, agent_name="aider"), result=make_run_result()
        )

    new_emit.assert_not_called()


def test_403_status_should_not_emit_transient_error(tmp_path):
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
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    new_emit.assert_not_called()


def test_malformed_jsonl_line_should_be_skipped_when_classifying(tmp_path):
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
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "rate_limit_account"


def test_missing_round_log_should_not_emit_or_crash(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    # round-99.log does not exist in tmp_path
    with patch(
        "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected"
    ) as new_emit:
        ClaudeErrorDetector().after_round(
            make_hook_context(tmp_path, round_num=99), result=make_run_result()
        )

    new_emit.assert_not_called()


_MOD = "agent_runner.builtin_plugins.claude_rate_limit"


def test_successful_round_should_emit_usage_event(tmp_path):
    """input_tokens is NET per Anthropic (cache_read_input_tokens excluded from the total)."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {"type": "assistant", "message": {"model": "claude-opus-4-7"}},
            {
                "type": "result",
                "is_error": False,
                "subtype": "success",
                "result": "done",
                "total_cost_usd": 0.0812,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 6,
                    "cache_read_input_tokens": 80,
                },
                "duration_ms": 14470,
            },
        ],
    )

    with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
        with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
            ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    err_emit.assert_not_called()
    usage_emit.assert_called_once()
    kwargs = usage_emit.call_args.kwargs
    assert kwargs["agent"] == "claude"
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["input_tokens"] == 100
    assert kwargs["output_tokens"] == 6
    assert kwargs["cached_tokens"] == 80
    assert kwargs["cost_usd"] == 0.0812
    assert kwargs["duration_ms"] == 14470
    assert kwargs["models_breakdown"] is None
    assert kwargs["cache_creation_tokens"] == 0
    assert kwargs["tool_call_count"] == 0


def test_5xx_error_should_emit_both_transient_error_and_usage_events(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {"type": "assistant", "message": {"model": "claude-opus-4-7"}},
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
            with patch("agent_runner.clock.SYSTEM_CLOCK.epoch", return_value=1000):
                ClaudeErrorDetector().after_round(
                    make_hook_context(tmp_path), result=make_run_result()
                )

    err_emit.assert_called_once()
    assert err_emit.call_args.kwargs["classification"] == "api_transient_5xx"
    usage_emit.assert_called_once()
    assert usage_emit.call_args.kwargs["agent"] == "claude"
    assert usage_emit.call_args.kwargs["model"] == "claude-opus-4-7"
    assert usage_emit.call_args.kwargs["input_tokens"] == 10


def test_assistant_event_should_populate_model_in_usage_event(tmp_path):
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
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    usage_emit.assert_called_once()
    assert usage_emit.call_args.kwargs["model"] == "claude-opus-4-7"


def test_missing_assistant_event_should_default_model_to_unknown(tmp_path):
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
            with patch("agent_runner.clock.SYSTEM_CLOCK.epoch", return_value=1000):
                ClaudeErrorDetector().after_round(
                    make_hook_context(tmp_path), result=make_run_result()
                )

    assert usage_emit.call_args.kwargs["model"] == "unknown"


def test_429_with_null_rate_limit_type_should_classify_as_rate_limit_model(tmp_path):
    """rate_limit_event with rateLimitType=null + api_error_status=429 must classify as
    rate_limit_model (infra), not rate_limit_account (5h quota).
    """
    from agent_runner.builtin_plugins.claude_rate_limit import _parse_claude_log

    log = tmp_path / "round-1.log"
    assistant_line = (
        '{"type":"assistant","message":{"model":"claude-opus-4-7",'
        '"content":[{"type":"text","text":"API Error: rate limited"}]}}\n'
    )
    result_line = (
        '{"type":"result","is_error":true,"api_error_status":429,'
        '"stop_reason":"stop_sequence","result":"API Error: rate limited",'
        '"usage":{"input_tokens":100,"output_tokens":10,"cache_read_input_tokens":0},'
        '"duration_ms":1000,"total_cost_usd":0.01}\n'
    )
    log.write_text(
        '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","rateLimitType":null}}\n'
        + assistant_line
        + result_line,
        encoding="utf-8",
    )

    with patch("agent_runner.clock.SYSTEM_CLOCK.epoch", return_value=1000):
        parsed = _parse_claude_log(log)

    assert parsed["transient_error"]["classification"] == "rate_limit_model"
    assert (
        parsed["transient_error"]["reset_at_epoch"] == 1060
    )  # now + 60s (_BACK_OFF_DEFAULTS["rate_limit_model"])


def test_rate_limit_event_with_null_type_and_no_result_should_have_no_transient_error(tmp_path):
    """Edge: rate_limit_event with rateLimitType=null but no result_event returns no
    transient_error (without a status code we can't bucket; supervisor uses generic retry).
    """
    from agent_runner.builtin_plugins.claude_rate_limit import _parse_claude_log

    log = tmp_path / "round-1.log"
    log.write_text(
        '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","rateLimitType":null}}\n',
        encoding="utf-8",
    )

    parsed = _parse_claude_log(log)

    assert "transient_error" not in parsed


def test_tool_use_blocks_should_populate_tool_call_count(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import _parse_claude_log

    log = tmp_path / "round-1.log"
    log.write_text(
        '{"type":"assistant","message":{"model":"claude-opus-4-7","content":[{"type":"tool_use","id":"a","name":"Read","input":{}}]}}\n'
        '{"type":"assistant","message":{"model":"claude-opus-4-7","content":[{"type":"text","text":"thinking"},{"type":"tool_use","id":"b","name":"Bash","input":{}}]}}\n'
        '{"type":"result","is_error":false,"usage":{"input_tokens":10,"output_tokens":5,"cache_read_input_tokens":0,"cache_creation_input_tokens":100},"duration_ms":1000,"total_cost_usd":0.01}\n',
        encoding="utf-8",
    )

    parsed = _parse_claude_log(log)

    assert parsed["usage"]["tool_call_count"] == 2
    assert parsed["usage"]["cache_creation_tokens"] == 100


def test_hook_context_phase_should_surface_in_usage_event(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {"type": "assistant", "message": {"model": "claude-opus-4-7", "content": []}},
            {
                "type": "result",
                "is_error": False,
                "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0},
                "duration_ms": 100,
                "total_cost_usd": 0.001,
            },
        ],
    )
    ctx = make_hook_context(tmp_path, agent_name="claude", phase="planning")
    result = make_run_result()

    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        ClaudeErrorDetector().after_round(ctx, result)

    kwargs = usage_emit.call_args.kwargs
    assert kwargs["phase"] == "planning"
    assert kwargs["success"] is True


def test_missing_phase_should_default_to_empty_string_in_usage_event(tmp_path):
    """HookContext.phase=None becomes phase='' in event (matches env-var contract)."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "is_error": False,
                "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0},
                "duration_ms": 100,
                "total_cost_usd": 0.001,
            }
        ],
    )
    ctx = make_hook_context(tmp_path, agent_name="claude", phase=None)
    result = make_run_result()

    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        ClaudeErrorDetector().after_round(ctx, result)

    kwargs = usage_emit.call_args.kwargs
    assert kwargs["phase"] == ""


def test_failed_round_should_set_success_false_in_usage_event(tmp_path):
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 500,
                "result": "500 err",
                "usage": {"input_tokens": 1, "output_tokens": 0, "cache_read_input_tokens": 0},
                "duration_ms": 100,
                "total_cost_usd": 0.001,
            }
        ],
    )
    ctx = make_hook_context(tmp_path, agent_name="claude")
    result = make_run_result(1)

    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected"):
            with patch("agent_runner.clock.SYSTEM_CLOCK.epoch", return_value=1000):
                ClaudeErrorDetector().after_round(ctx, result)

    kwargs = usage_emit.call_args.kwargs
    assert kwargs["success"] is False


def test_custom_agent_name_with_claude_binary_should_emit_usage_event(tmp_path):
    """Regression: 0.1.29 bug — strict ctx.agent_name == "claude" guard
    suppressed events when users set [agent] name = "<custom>". Fix uses
    agent_binary basename instead.
    """
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    round_log = log_dir / "round-1.log"
    round_log.write_text(
        '{"type":"result","is_error":false,"usage":{"input_tokens":10,"output_tokens":5,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":50},'
        '"duration_ms":1000,"total_cost_usd":0.01}\n',
        encoding="utf-8",
    )
    ctx = make_hook_context(
        tmp_path,
        agent_name="acme_dev",
        agent_binary="claude",
        agent_log_path=round_log,
    )
    result = make_run_result()

    with patch(f"{_MOD}.emit_agent_usage_recorded") as emit:
        ClaudeErrorDetector().after_round(ctx, result)

    emit.assert_called_once()


_EDIT_EVT = (
    '{"type":"assistant","message":{"model":"claude-opus-4-7",'
    '"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"/x.md"}}]}}'
)
_READ_EVT = (
    '{"type":"assistant","message":{"model":"x",'
    '"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/y.md"}}]}}'
)
_RESULT_LINE = (
    '{"type":"result","is_error":false,"usage":{"input_tokens":1,'
    '"output_tokens":1,"cache_read_input_tokens":0,'
    '"cache_creation_input_tokens":0},"duration_ms":100,"total_cost_usd":0.001}'
)
_EDIT_EVT_MODEL_X = (
    '{"type":"assistant","message":{"model":"x",'
    '"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"/x.md"}}]}}'
)


@pytest.mark.parametrize(
    "lines, anomaly_window, anomaly_threshold, expected_anomaly",
    [
        pytest.param([_EDIT_EVT] * 5 + [_RESULT_LINE], 10, 8, None, id="under_threshold"),
        pytest.param(
            [_EDIT_EVT] * 8 + [_RESULT_LINE],
            10,
            8,
            {"tool_name": "Edit", "target": "/x.md", "count": 8},
            id="at_threshold",
        ),
        pytest.param(
            [_EDIT_EVT_MODEL_X, _READ_EVT] * 4 + [_RESULT_LINE], 10, 8, None, id="mixed_tools"
        ),
        pytest.param(
            [_EDIT_EVT_MODEL_X] * 10 + [_RESULT_LINE], 10, 0, None, id="threshold_disabled"
        ),
    ],
)
def test_repetitive_tool_should_emit_anomaly_only_when_at_or_above_threshold(
    tmp_path, lines, anomaly_window, anomaly_threshold, expected_anomaly
):
    from agent_runner.builtin_plugins.claude_rate_limit import _parse_claude_log

    log = tmp_path / "round-1.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    parsed = _parse_claude_log(
        log, anomaly_window=anomaly_window, anomaly_threshold=anomaly_threshold
    )

    if expected_anomaly is None:
        assert "anomaly" not in parsed
    else:
        assert parsed["anomaly"]["tool_name"] == expected_anomaly["tool_name"]
        assert parsed["anomaly"]["target"] == expected_anomaly["target"]
        assert parsed["anomaly"]["count"] == expected_anomaly["count"]


def test_non_claude_binary_should_not_emit_usage_event(tmp_path):
    """ClaudeErrorDetector must NOT fire for non-claude binaries
    (e.g. aider, custom CLI). Prevents cross-pollination.
    """
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    round_log = log_dir / "round-1.log"
    round_log.write_text(
        '{"type":"result","is_error":false,"usage":{"input_tokens":10,"output_tokens":5,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":50},'
        '"duration_ms":1000,"total_cost_usd":0.01}\n',
        encoding="utf-8",
    )
    ctx = make_hook_context(
        tmp_path,
        agent_name="aider",
        agent_binary="aider",
        agent_log_path=round_log,
    )
    result = make_run_result()

    with patch(f"{_MOD}.emit_agent_usage_recorded") as emit:
        ClaudeErrorDetector().after_round(ctx, result)

    emit.assert_not_called()


def test_529_overloaded_status_should_classify_as_api_transient_5xx(tmp_path):
    """Anthropic's "overloaded" status (529) should classify as api_transient_5xx,
    not fall through as unknown error. Real scar — gateway hits this during
    sustained Anthropic capacity issues.
    """
    from agent_runner.builtin_plugins.claude_rate_limit import _parse_claude_log

    log = tmp_path / "round-1.log"
    log.write_text(
        '{"type":"result","is_error":true,"api_error_status":529,"result":"Overloaded"}\n',
        encoding="utf-8",
    )

    parsed = _parse_claude_log(log)

    assert parsed["transient_error"]["classification"] == "api_transient_5xx"


def test_emit_transient_error_detected_should_redact_secrets_in_raw_field(tmp_path):
    from agent_runner._emit import emit_transient_error_detected

    emit_transient_error_detected(
        tmp_path,
        classification="api_transient_5xx",
        agent="claude",
        reset_at_epoch=0,
        round_num=1,
        raw="boom Bearer sk-ant-LEAKvalue000111 tail",
    )

    payload = json.loads(sorted(tmp_path.glob("events-*.jsonl"))[-1].read_text().splitlines()[-1])
    assert "sk-ant-LEAKvalue000111" not in payload["raw"] and "<redacted>" in payload["raw"]


def test_terminal_event_should_still_classify_when_followed_by_stderr_burst(tmp_path):
    """A stderr burst of ANY size after the terminal JSONL event must not
    evict it: json_tail filters non-JSON chatter BEFORE windowing (1000
    lines >> _TAIL_LINES pins the class-elimination, not a window size)."""
    from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector

    log_path = write_round_log(
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
    with log_path.open("a") as f:
        for i in range(1000):
            f.write(f"stderr chatter line {i}: retrying connection...\n")

    with patch(f"{_MOD}.emit_transient_error_detected") as new_emit:
        ClaudeErrorDetector().after_round(make_hook_context(tmp_path), result=make_run_result())

    new_emit.assert_called_once()
    assert new_emit.call_args.kwargs["classification"] == "api_transient_5xx"
