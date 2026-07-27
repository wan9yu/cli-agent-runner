"""Unit tests for PiErrorDetector (usage aggregation + transient classifier).

Fixtures are real records captured from Pi Coding Agent 0.80.10
`pi -p -na --mode json --model <provider>/<model>` runs (session/response ids
replaced with placeholders, long thinking text abbreviated):

- the success + auto-retry records come from a real moonshot/kimi-k3 round;
- the 401 record comes from the same provider with a deliberately invalid key;
- the 429/503 records come from pointing a scratch provider's baseUrl at a
  local endpoint returning those statuses (no upstream traffic);
- the multi-turn tool-call records come from a local endpoint that answers
  successfully, which is how the per-message (not cumulative) usage semantics
  were established.

The pinned surprise: pi exits **0** on provider failure, so every failure test
passes ``exit_code=0`` — the round-failed signal has to come from the stream.
"""

from __future__ import annotations

from unittest.mock import patch

from tests._test_helpers import (
    make_hook_context,
    make_run_result,
    write_round_log,
    write_round_log_text,
)

_MOD = "agent_runner.builtin_plugins.pi"

_SESSION = {
    "type": "session",
    "version": 3,
    "id": "00000000-0000-0000-0000-000000000000",
    "timestamp": "2026-07-21T00:36:56.553Z",
    "cwd": "/tmp/pi-round-test",
}


def _assistant(
    content: list,
    *,
    inp: int = 0,
    out: int = 0,
    cache: int = 0,
    cost: float = 0,
    stop: str = "stop",
    provider: str = "mockok",
    model: str = "mock-ok",
    ts: int = 1785131556045,
    error: str | None = None,
) -> dict:
    """A pi assistant message. Same shape as ``_ASSISTANT_OK``, which is kept
    spelled out below as the one full-fidelity specimen."""
    message = {
        "role": "assistant",
        "content": content,
        "api": "openai-completions",
        "provider": provider,
        "model": model,
        "usage": {
            "input": inp,
            "output": out,
            "cacheRead": cache,
            "cacheWrite": 0,
            "totalTokens": inp + out + cache,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": cost},
        },
        "stopReason": stop,
        "timestamp": ts,
    }
    if error is not None:
        message["errorMessage"] = error
    return message


# --- real moonshot/kimi-k3 round: attempt 1 timed out, retry succeeded --------
_ASSISTANT_TIMEOUT = _assistant(
    [],
    stop="error",
    provider="moonshot",
    model="kimi-k3",
    ts=1784594216632,
    error="Request timed out.",
)
_ASSISTANT_OK = {
    "role": "assistant",
    "content": [
        {
            "type": "thinking",
            "thinking": "The user asks me to reply with the single word OK.",
            "thinkingSignature": "reasoning_content",
        },
        {"type": "text", "text": "OK"},
    ],
    "api": "openai-completions",
    "provider": "moonshot",
    "model": "kimi-k3",
    "usage": {
        "input": 793,
        "output": 45,
        "cacheRead": 768,
        "cacheWrite": 0,
        "reasoning": 29,
        "totalTokens": 1606,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
    },
    "stopReason": "stop",
    "timestamp": 1784594229160,
    "responseId": "chatcmpl-000000000000000000000000",
}
_MSG_END_TIMEOUT = {"type": "message_end", "message": _ASSISTANT_TIMEOUT}
_MSG_END_OK = {"type": "message_end", "message": _ASSISTANT_OK}
_AGENT_END_TIMEOUT = {
    "type": "agent_end",
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "ping"}], "timestamp": 1784594216584},
        _ASSISTANT_TIMEOUT,
    ],
    "willRetry": True,
}
_AGENT_END_OK = {"type": "agent_end", "messages": [_ASSISTANT_OK], "willRetry": False}
_AUTO_RETRY_START = {
    "type": "auto_retry_start",
    "attempt": 1,
    "maxAttempts": 3,
    "delayMs": 2000,
    "errorMessage": "Request timed out.",
}
_AUTO_RETRY_END_OK = {"type": "auto_retry_end", "success": True, "attempt": 1}
_AGENT_SETTLED = {"type": "agent_settled"}

# A thinking delta: note it repeats the whole message state, with zeroed usage
# and a stale ``stopReason`` — parsing these would poison both emissions.
_MSG_UPDATE_THINKING = {
    "type": "message_update",
    "message": {
        "role": "assistant",
        "content": [{"type": "thinking", "thinking": "The user asks me"}],
        "provider": "moonshot",
        "model": "kimi-k3",
        "usage": {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 0,
            "cost": {"total": 0},
        },
        "stopReason": "stop",
        "timestamp": 1784594229160,
    },
}


def _failing_assistant(error_message: str, provider: str = "mock") -> dict:
    """Assistant message as pi writes it when the provider call failed."""
    return _assistant(
        [],
        stop="error",
        provider=provider,
        model="mock-model",
        ts=1785131425512,
        error=error_message,
    )


_ERR_429 = "429 status code (no body)"
_ERR_503 = "503 status code (no body)"
_ERR_401 = '401: {"message":"Invalid Authentication","type":"invalid_authentication_error"}'

# --- real multi-turn round (tool call then final answer) ----------------------
# The one fixture with catalog pricing behind it, so cost_usd has a money path.
_ASSISTANT_TOOLCALL = _assistant(
    [{"type": "toolCall", "id": "call_000000", "name": "bash", "arguments": {"cmd": "echo hi"}}],
    inp=60,
    out=20,
    cache=40,
    stop="toolUse",
)
_ASSISTANT_AFTER_TOOL = _assistant(
    [{"type": "text", "text": "DONE"}],
    inp=210,
    out=7,
    cache=90,
    cost=0.0031,
    ts=1785131556065,
)
_AGENT_END_MULTI_TURN = {
    "type": "agent_end",
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "say hi"}]},
        _ASSISTANT_TOOLCALL,
        {"role": "toolResult", "toolCallId": "call_000000", "toolName": "bash", "isError": False},
        _ASSISTANT_AFTER_TOOL,
    ],
    "willRetry": False,
}


def _ok_round():
    return make_run_result()


def _run(tmp_path, events, result=None, agent_name="pi"):
    """Write a round log, run the detector, return (usage_mock, error_mock)."""
    from agent_runner.builtin_plugins.pi import PiErrorDetector

    write_round_log(tmp_path, 1, events)
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
            with patch(f"{_MOD}.time.time", return_value=1000):
                PiErrorDetector().after_round(
                    make_hook_context(tmp_path, agent_name=agent_name),
                    result=result or _ok_round(),
                )
    return usage_emit, err_emit


def test_given_successful_round_when_after_round_then_usage_from_final_message(tmp_path):
    usage_emit, err_emit = _run(tmp_path, [_SESSION, _MSG_END_OK, _AGENT_END_OK, _AGENT_SETTLED])
    err_emit.assert_not_called()
    usage_emit.assert_called_once()
    kw = usage_emit.call_args.kwargs
    assert kw["agent"] == "pi"
    assert kw["model"] == "moonshot/kimi-k3"
    assert kw["input_tokens"] == 793
    assert kw["output_tokens"] == 45
    assert kw["cached_tokens"] == 768
    assert kw["cache_creation_tokens"] == 0
    assert kw["cost_usd"] is None  # pi reports cost 0 without catalog pricing
    assert kw["duration_ms"] == 12607  # session header -> final message timestamp
    assert kw["tool_call_count"] == 0
    assert kw["success"] is True


def test_given_multi_turn_round_when_after_round_then_usage_summed_across_messages(tmp_path):
    """pi's usage is per-message, not cumulative: reading only the last message
    would drop every earlier turn of a tool-using round."""
    usage_emit, err_emit = _run(tmp_path, [_SESSION, _AGENT_END_MULTI_TURN, _AGENT_SETTLED])
    err_emit.assert_not_called()
    kw = usage_emit.call_args.kwargs
    assert kw["input_tokens"] == 270  # 60 + 210
    assert kw["output_tokens"] == 27  # 20 + 7
    assert kw["cached_tokens"] == 130  # 40 + 90
    assert kw["cost_usd"] == 0.0031  # usage.cost.total, summed the same way
    assert kw["tool_call_count"] == 1
    assert kw["model"] == "mockok/mock-ok"


def test_given_retry_then_success_when_after_round_then_final_usage_and_no_transient(tmp_path):
    """pi self-retries up to 3x; a blip it recovered from is not a
    supervisor-level transient error, and the post-retry state is authoritative."""
    usage_emit, err_emit = _run(
        tmp_path,
        [
            _SESSION,
            _MSG_END_TIMEOUT,
            _AGENT_END_TIMEOUT,
            _AUTO_RETRY_START,
            _MSG_UPDATE_THINKING,
            _MSG_END_OK,
            _AUTO_RETRY_END_OK,
            _AGENT_END_OK,
            _AGENT_SETTLED,
        ],
    )
    err_emit.assert_not_called()
    kw = usage_emit.call_args.kwargs
    assert kw["input_tokens"] == 793  # failed attempt contributed zeros
    assert kw["output_tokens"] == 45
    assert kw["success"] is True


def test_given_exhausted_429_retries_when_after_round_then_rate_limit_model(tmp_path):
    """pi exits 0 even after burning all 3 retries — the stream is the only
    truthful failure signal."""
    failed = _failing_assistant(_ERR_429)
    usage_emit, err_emit = _run(
        tmp_path,
        [
            _SESSION,
            {"type": "message_end", "message": failed},
            {"type": "agent_end", "messages": [failed], "willRetry": False},
            {"type": "auto_retry_end", "success": False, "attempt": 3, "finalError": _ERR_429},
            _AGENT_SETTLED,
        ],
    )
    err_emit.assert_called_once()
    kw = err_emit.call_args.kwargs
    assert kw["classification"] == "rate_limit_model"
    assert kw["agent"] == "pi"
    assert kw["reset_at_epoch"] == 1060  # now + 60s default back-off
    assert _ERR_429 in kw["raw"]
    # nothing reached the model, so there is no cost record to publish
    usage_emit.assert_not_called()


def test_given_auth_failure_when_after_round_then_no_transient_and_no_usage(tmp_path):
    """A 401 is permanent until an operator fixes config — oauth_fail territory
    (the monitor's default auth_fail_patterns match this wording), not a
    transient bucket. pi does not retry it and still exits 0."""
    failed = _failing_assistant(_ERR_401, provider="moonshot")
    usage_emit, err_emit = _run(
        tmp_path,
        [
            _SESSION,
            {"type": "message_end", "message": failed},
            {"type": "agent_end", "messages": [failed], "willRetry": False},
            _AGENT_SETTLED,
        ],
    )
    err_emit.assert_not_called()
    usage_emit.assert_not_called()


def test_given_round_killed_before_agent_end_when_after_round_then_usage_from_message_ends(
    tmp_path,
):
    """A supervisor round-timeout kill leaves no agent_end record; the completed
    message_end records still hold the tokens that were actually spent."""
    usage_emit, err_emit = _run(
        tmp_path,
        [_SESSION, _MSG_UPDATE_THINKING, _MSG_END_OK],
        result=make_run_result(124, timed_out=True),
    )
    err_emit.assert_not_called()  # no classifiable provider error in the stream
    kw = usage_emit.call_args.kwargs
    assert kw["input_tokens"] == 793
    assert kw["success"] is False


def test_given_only_thinking_deltas_when_after_round_then_no_emit(tmp_path):
    """message_update repeats the full message state with zeroed usage and a
    stale stopReason; treating one as terminal would publish a phantom round."""
    usage_emit, err_emit = _run(tmp_path, [_SESSION] + [_MSG_UPDATE_THINKING] * 5)
    usage_emit.assert_not_called()
    err_emit.assert_not_called()


def test_given_non_pi_binary_when_after_round_then_no_emit(tmp_path):
    usage_emit, err_emit = _run(
        tmp_path, [_SESSION, _MSG_END_OK, _AGENT_END_OK], agent_name="claude"
    )
    usage_emit.assert_not_called()
    err_emit.assert_not_called()


def test_given_missing_round_log_when_after_round_then_no_crash(tmp_path):
    from agent_runner.builtin_plugins.pi import PiErrorDetector

    ctx = make_hook_context(tmp_path, agent_name="pi")
    assert not ctx.agent_log_path.exists()
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
            PiErrorDetector().after_round(ctx, result=_ok_round())
    usage_emit.assert_not_called()
    err_emit.assert_not_called()


def test_given_plain_text_chatter_when_after_round_then_tolerated(tmp_path):
    """The round log merges stdout+stderr; non-JSON lines must not crash the
    parser nor block the usage record on the JSON lines around them.

    Real rounds open with node warnings on stderr, so the ``session`` header
    is routinely not line 1 — anchoring the duration on the first line alone
    would silently report 0 for every such round."""
    from agent_runner.builtin_plugins.pi import PiErrorDetector

    log_path = write_round_log(tmp_path, 1, [_SESSION, _MSG_END_OK, _AGENT_END_OK])
    write_round_log_text(
        tmp_path,
        1,
        "node:internal/process/warning: ExperimentalWarning\n"
        + log_path.read_text()
        + "some trailing stderr chatter\n",
    )
    with patch(f"{_MOD}.emit_agent_usage_recorded") as usage_emit:
        with patch(f"{_MOD}.emit_transient_error_detected"):
            PiErrorDetector().after_round(
                make_hook_context(tmp_path, agent_name="pi"), result=_ok_round()
            )
    kw = usage_emit.call_args.kwargs
    assert kw["input_tokens"] == 793
    assert kw["duration_ms"] == 12607  # session header found past the chatter


def test_classify_pi_error_maps_only_observed_shapes():
    """Lock the errorMessage → bucket mapping to shapes captured from pi 0.80.10."""
    from agent_runner.builtin_plugins.pi import _classify_pi_error

    assert _classify_pi_error(_ERR_429) == "rate_limit_model"
    assert _classify_pi_error(_ERR_503) == "api_transient_5xx"
    assert _classify_pi_error("500 status code (no body)") == "api_transient_5xx"
    assert _classify_pi_error("408 status code (no body)") == "api_timeout"
    assert _classify_pi_error("Request timed out.") == "api_timeout"
    # permanent until an operator intervenes: auth, unknown model, bad request
    assert _classify_pi_error(_ERR_401) is None
    assert _classify_pi_error('404: {"message":"model not found"}') is None
    assert _classify_pi_error('400: {"message":"bad request"}') is None
    assert _classify_pi_error("") is None
    assert _classify_pi_error(None) is None
