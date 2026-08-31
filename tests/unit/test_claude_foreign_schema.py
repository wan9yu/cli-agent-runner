from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from agent_runner.builtin_plugins.claude_rate_limit import ClaudeErrorDetector, _parse_claude_log


def _write(tmp: Path, lines: list[dict]) -> Path:
    p = tmp / "R1-x.log"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def test_rate_limit_info_null_does_not_crash_parse(tmp_path: Path) -> None:
    log = _write(
        tmp_path,
        [
            {"type": "rate_limit_event", "rate_limit_info": None},
            {"type": "result", "is_error": False, "usage": {"input_tokens": 3, "output_tokens": 1}},
        ],
    )
    parsed = _parse_claude_log(log)  # must not raise AttributeError
    assert "usage" in parsed  # usage still extracted


def test_resets_at_null_still_emits_usage_and_detection(tmp_path: Path) -> None:
    log = _write(
        tmp_path,
        [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "rejected",
                    "rateLimitType": "five_hour",
                    "resetsAt": None,
                },
            },
            {
                "type": "result",
                "is_error": True,
                "usage": {"input_tokens": 5, "output_tokens": 2},
                "total_cost_usd": 0.1,
            },
        ],
    )
    parsed = _parse_claude_log(log)
    assert parsed["transient_error"]["classification"] == "rate_limit_account"
    assert isinstance(parsed["transient_error"]["reset_at_epoch"], int)  # fallback, not TypeError
    assert "usage" in parsed


def test_one_emit_failure_does_not_void_the_others(tmp_path: Path) -> None:
    log = _write(
        tmp_path,
        [
            {
                "type": "result",
                "is_error": True,
                "api_error_status": 429,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ],
    )
    ctx = MagicMock(
        agent_binary="claude",
        agent_log_path=log,
        log_dir=tmp_path,
        round_num=1,
        phase="p",
        anomaly_repetitive_window=0,
        anomaly_repetitive_threshold=0,
    )
    det = ClaudeErrorDetector()
    with (
        __import__("unittest").mock.patch(
            "agent_runner.builtin_plugins.claude_rate_limit.emit_transient_error_detected",
            side_effect=OSError("disk full"),
        ),
        __import__("unittest").mock.patch(
            "agent_runner.builtin_plugins.claude_rate_limit.emit_agent_usage_recorded",
        ) as usage,
    ):
        det.after_round(ctx, MagicMock(ok=False))
    usage.assert_called_once()  # usage still emitted though detection emit failed
