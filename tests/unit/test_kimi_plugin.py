"""Unit tests for KimiErrorDetector (retry-record classifier; no usage source).

Fixtures are real records captured from Kimi Code CLI 0.29.1
`kimi --output-format stream-json -p ...` (session ids replaced with
placeholders). The retry records came from pointing KIMI_MODEL_BASE_URL at a
local endpoint returning 429/503, so they are genuine CLI output rather than
hand-written shapes.
"""

from __future__ import annotations

from unittest.mock import patch

from tests._test_helpers import (
    make_hook_context,
    make_run_result,
    write_round_log,
    write_round_log_text,
)

_MOD = "agent_runner.builtin_plugins.kimi"

# Real captured `turn.step.retrying` record (429 from the provider).
_RETRY_429 = {
    "role": "meta",
    "type": "turn.step.retrying",
    "failed_attempt": 1,
    "next_attempt": 2,
    "max_attempts": 10,
    "delay_ms": 566.6868929526177,
    "error_name": "APIProviderRateLimitError",
    "error_message": "429 Your account has hit the rate limit, please retry later",
    "status_code": 429,
}
_RETRY_503 = {
    "role": "meta",
    "type": "turn.step.retrying",
    "failed_attempt": 1,
    "next_attempt": 2,
    "max_attempts": 10,
    "delay_ms": 1134.5171852413182,
    "error_name": "APIStatusError",
    "error_message": "503 upstream unavailable",
    "status_code": 503,
}
# Real captured terminal record of a completed round.
_RESUME_HINT = {
    "role": "meta",
    "type": "session.resume_hint",
    "session_id": "session_00000000-0000-0000-0000-000000000000",
    "command": "kimi -r session_00000000-0000-0000-0000-000000000000",
    "content": "To resume this session: kimi -r session_00000000-0000-0000-0000-000000000000",
}
_ASSISTANT = {"role": "assistant", "content": "pong"}


def _failed_round():
    """Round killed by the supervisor's round timeout while kimi kept retrying."""
    return make_run_result(124, timed_out=True)


def _run(tmp_path, log, *, result=None, agent_name="kimi"):
    """Write a round log (JSONL events or raw text), run the detector, return
    the transient-error emit mock. Defaults to a failed round, which is the
    only kind kimi's detector looks at."""
    from agent_runner.builtin_plugins.kimi import KimiErrorDetector

    if isinstance(log, str):
        write_round_log_text(tmp_path, 1, log)
    else:
        write_round_log(tmp_path, 1, log)
    with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
        with patch(f"{_MOD}.time.time", return_value=1000):
            KimiErrorDetector().after_round(
                make_hook_context(tmp_path, agent_name=agent_name),
                result=result if result is not None else _failed_round(),
            )
    return err_emit


def test_given_429_retry_on_failed_round_when_after_round_then_rate_limit_model(tmp_path):
    err_emit = _run(tmp_path, [_RETRY_429, _RETRY_429])
    err_emit.assert_called_once()
    kw = err_emit.call_args.kwargs
    assert kw["classification"] == "rate_limit_model"
    assert kw["agent"] == "kimi"
    assert kw["reset_at_epoch"] == 1060  # now + 60s default back-off
    assert "429 Your account has hit the rate limit" in kw["raw"]


def test_given_503_retry_on_failed_round_when_after_round_then_api_transient_5xx(tmp_path):
    err_emit = _run(tmp_path, [_RETRY_503, _RESUME_HINT])
    err_emit.assert_called_once()
    assert err_emit.call_args.kwargs["classification"] == "api_transient_5xx"


def test_given_retry_absorbed_by_successful_round_when_after_round_then_no_emit(tmp_path):
    """kimi retries internally (max_attempts 10); a blip it recovered from is not
    a supervisor-level transient error — backing off after a successful round
    would be a false alarm."""
    err_emit = _run(tmp_path, [_RETRY_429, _ASSISTANT, _RESUME_HINT], result=make_run_result())
    err_emit.assert_not_called()


def test_given_non_kimi_binary_when_after_round_then_no_emit(tmp_path):
    err_emit = _run(tmp_path, [_RETRY_429], agent_name="claude")
    err_emit.assert_not_called()


def test_given_missing_round_log_when_after_round_then_no_crash(tmp_path):
    from agent_runner.builtin_plugins.kimi import KimiErrorDetector

    ctx = make_hook_context(tmp_path, agent_name="kimi")
    assert not ctx.agent_log_path.exists()
    with patch(f"{_MOD}.emit_transient_error_detected") as err_emit:
        KimiErrorDetector().after_round(ctx, result=_failed_round())
    err_emit.assert_not_called()


def test_given_plain_text_stderr_error_when_after_round_then_tolerated(tmp_path):
    """The round log merges stdout+stderr: kimi's fatal errors arrive as plain
    text (real captured 401 wording). Non-JSON lines must not crash the parser,
    and auth failure is oauth_fail territory, not a transient bucket."""
    err_emit = _run(
        tmp_path,
        "error: failed to run prompt: provider.auth_error: 401 Invalid Authentication\n"
        "See log: /Users/dev/.kimi-code/logs/kimi-code.log\n",
        result=make_run_result(1),
    )
    err_emit.assert_not_called()


def test_given_completed_round_when_after_round_then_no_usage_event_written(tmp_path):
    """Pins the Phase-A finding: kimi's stream-json writer emits no token
    counters, so no agent_usage_recorded is fabricated. Asserted through the
    real emitter path (no patching) so a future usage source has to update this.
    """
    from agent_runner.builtin_plugins.kimi import KimiErrorDetector
    from agent_runner.events import AGENT_USAGE_RECORDED

    write_round_log(tmp_path, 1, [_ASSISTANT, _RESUME_HINT])
    KimiErrorDetector().after_round(
        make_hook_context(tmp_path, agent_name="kimi"),
        result=make_run_result(),
    )
    emitted = "".join(p.read_text() for p in tmp_path.glob("events-*.jsonl"))
    assert AGENT_USAGE_RECORDED not in emitted
