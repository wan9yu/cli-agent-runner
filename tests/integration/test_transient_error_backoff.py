"""Integration tests for transient-error supervisor back-off.

Tests the supervisor's response to transient_error_detected and legacy
rate_limit_rejected events in the events.jsonl log.

Note: plugin detection (reading round logs) is tested in
tests/unit/test_claude_error_detector.py. These tests focus on the
supervisor loop: _check_throttle_state + action dispatch.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from tests._test_helpers import make_toml_with_sections, read_events_for_current_month


def _seed_transient_error_event(log_dir: Path, classification: str, *, future_s: int = 60) -> None:
    """Write a transient_error_detected event to events.jsonl so the supervisor sees it."""
    log_dir.mkdir(parents=True, exist_ok=True)
    month = datetime.now(UTC).strftime("%Y-%m")
    events_path = log_dir / f"events-{month}.jsonl"
    future = int(time.time() + future_s)
    events_path.write_text(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "event": "transient_error_detected",
                "classification": classification,
                "agent": "claude",
                "reset_at_epoch": future,
                "round_num": 1,
                "raw": "test error",
            }
        )
        + "\n"
    )


def _seed_legacy_rate_limit_event(log_dir: Path, *, future_s: int = 60) -> None:
    """Write a legacy rate_limit_rejected event to events.jsonl."""
    log_dir.mkdir(parents=True, exist_ok=True)
    month = datetime.now(UTC).strftime("%Y-%m")
    events_path = log_dir / f"events-{month}.jsonl"
    future = int(time.time() + future_s)
    events_path.write_text(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "event": "rate_limit_rejected",
                "agent": "claude",
                "reset_at_epoch": future,
                "limit_type": "five_hour",
                "round_num": 1,
                "raw": "rate limited",
            }
        )
        + "\n"
    )


def test_given_5xx_detected_event_when_serve_skip_then_no_sleep(tmp_path: Path):
    """transient_error_action = skip -> supervisor proceeds past transient error immediately."""
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra='transient_error_action = "skip"\nrestart_delay_s = 1\n',
    )
    log_dir = tmp_path / "logs"
    _seed_transient_error_event(log_dir, "api_transient_5xx")

    start = time.time()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
            "--max-rounds",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    duration = time.time() - start
    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    # skip means no sleep; completes quickly
    assert duration < 30, f"unexpected duration: {duration:.1f}s"
    events = read_events_for_current_month(log_dir)
    # The seeded detected event is still there
    detected = [e for e in events if e.get("event") == "transient_error_detected"]
    assert len(detected) >= 1
    assert detected[0]["classification"] == "api_transient_5xx"


def test_given_transient_error_action_stop_when_5xx_detected_then_terminates_early(tmp_path: Path):
    """transient_error_action = stop -> supervisor stops on first transient error detection."""
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra='transient_error_action = "stop"\nrestart_delay_s = 1\n',
    )
    log_dir = tmp_path / "logs"
    _seed_transient_error_event(log_dir, "api_transient_5xx")

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
            "--max-rounds",
            "5",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = read_events_for_current_month(log_dir)
    self_term = [e for e in events if e.get("event") == "agent_self_terminated"]
    # supervisor stopped early due to stop action
    assert len(self_term) >= 1
    assert self_term[0].get("reason") == "rate_limit"


def test_given_legacy_rate_limit_event_when_supervisor_reads_then_classification_account(
    tmp_path: Path,
):
    """Legacy rate_limit_rejected events are read and imply rate_limit_account classification."""
    from agent_runner._throttle import _check_throttle_state

    log_dir = tmp_path / "logs"
    _seed_legacy_rate_limit_event(log_dir)

    state = _check_throttle_state(log_dir)
    assert state is not None
    assert state.classification == "rate_limit_account"
    assert state.agent == "claude"


def test_given_rate_limit_action_alias_when_serve_skip_then_supervisor_uses_value(tmp_path: Path):
    """Old rate_limit_action = skip config propagates to transient_error_action correctly."""
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra='rate_limit_action = "skip"\nrestart_delay_s = 1\n',
    )
    log_dir = tmp_path / "logs"
    _seed_transient_error_event(log_dir, "api_transient_5xx")

    start = time.time()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
            "--max-rounds",
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    duration = time.time() - start
    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    # skip means no sleep; completes quickly even with seeded transient error
    assert duration < 30, f"unexpected duration: {duration:.1f}s"
