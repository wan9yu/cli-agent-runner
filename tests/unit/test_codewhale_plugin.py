"""Unit tests for CodewhaleErrorDetector (usage; classify-only-what-maps)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tests._test_helpers import make_hook_context, write_round_log

_MOD = "agent_runner.builtin_plugins.codewhale"


def test_given_success_round_when_after_round_then_usage_emitted_from_metadata(tmp_path):
    from agent_runner.builtin_plugins.codewhale import CodewhaleErrorDetector

    # Real captured codewhale exec stream-json terminal records.
    write_round_log(
        tmp_path,
        1,
        [
            {"type": "content", "content": "working..."},
            {"type": "tool_result", "id": "c1", "output": "ok", "status": "success"},
            {
                "type": "metadata",
                "meta": {
                    "model": "deepseek-v4-pro",
                    "input_tokens": 66014,
                    "output_tokens": 303,
                    "session_id": "f029d9a9",
                    "status": "completed",
                },
            },
            {"type": "done"},
        ],
    )
    result = MagicMock(exit_code=0, timed_out=False)
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
            CodewhaleErrorDetector().after_round(
                make_hook_context(tmp_path, agent_name="codewhale"), result=result
            )
    usage_emit.assert_called_once()
    kw = usage_emit.call_args.kwargs
    assert kw["agent"] == "codewhale"
    assert kw["model"] == "deepseek-v4-pro"
    assert kw["input_tokens"] == 66014
    assert kw["output_tokens"] == 303
    assert kw["cost_usd"] is None
    assert kw["cached_tokens"] == 0
    err_emit.assert_not_called()


def test_given_non_codewhale_binary_when_after_round_then_no_emit(tmp_path):
    from agent_runner.builtin_plugins.codewhale import CodewhaleErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {
                "type": "metadata",
                "meta": {
                    "model": "x",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "status": "completed",
                },
            }
        ],
    )
    result = MagicMock(exit_code=0, timed_out=False)
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        CodewhaleErrorDetector().after_round(
            make_hook_context(tmp_path, agent_name="claude"), result=result
        )
    usage_emit.assert_not_called()


def test_given_auth_error_round_when_after_round_then_no_transient_error(tmp_path):
    """Auth failure is NOT a transient bucket (it's oauth_fail territory) -> usage only."""
    from agent_runner.builtin_plugins.codewhale import CodewhaleErrorDetector

    write_round_log(
        tmp_path,
        1,
        [
            {"type": "error", "error": "Authentication failed: invalid key"},
            {
                "type": "metadata",
                "meta": {
                    "model": "deepseek-v4-pro",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "status": "failed",
                },
            },
            {"type": "done"},
        ],
    )
    result = MagicMock(exit_code=1, timed_out=False)
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
            CodewhaleErrorDetector().after_round(
                make_hook_context(tmp_path, agent_name="codewhale"), result=result
            )
    err_emit.assert_not_called()  # auth error does not map to a transient bucket
    usage_emit.assert_called_once()  # usage still emitted (status:failed round)


def test_given_non_json_lines_when_after_round_then_tolerated(tmp_path):
    """Real codewhale stdout has terminal-escape non-JSON lines; parser must skip them."""
    from agent_runner.builtin_plugins.codewhale import CodewhaleErrorDetector

    # Write raw lines manually (write_round_log only emits JSON dicts).
    # Path must match make_hook_context default: tmp_path/rounds/R1-test.log
    rounds_dir = tmp_path / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    meta_line = (
        '{"type":"metadata","meta":{'
        '"model":"deepseek-v4-pro","input_tokens":5,"output_tokens":2,'
        '"status":"completed"}}'
    )
    (rounds_dir / "R1-test.log").write_text(
        "\x1b]9;4;1\x07\x1b]0;\U0001f433 CodeWhale\x07"
        '{"type":"content","content":"hi"}\n' + meta_line + "\n"
        "not json at all\n"
        '{"type":"done"}\n',
        encoding="utf-8",
    )
    result = MagicMock(exit_code=0, timed_out=False)
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        CodewhaleErrorDetector().after_round(
            make_hook_context(tmp_path, agent_name="codewhale"), result=result
        )
    usage_emit.assert_called_once()
    assert usage_emit.call_args.kwargs["input_tokens"] == 5
