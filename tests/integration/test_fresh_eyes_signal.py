"""Integration tests for fresh_eyes signal injection in the serve loop."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests._test_helpers import make_toml_with_sections, read_events_for_current_month


def test_given_fresh_eyes_every_n_2_when_round_2_runs_then_trigger_event_emitted(
    tmp_path: Path,
):
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra=("restart_delay_s = 1\nfresh_eyes_every_n = 2\n"),
        vcs_block='[vcs]\ndirty_action = "ignore"\n',
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    log_dir = tmp_path / "logs"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
            "--max-rounds",
            "3",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = read_events_for_current_month(log_dir)
    fresh_events = [e for e in events if e.get("event") == "fresh_eyes_round_triggered"]
    # Round 2 only triggers (round_num is 1,2,3; only round_num=2 is multiple of 2 and >0)
    assert len(fresh_events) == 1
    assert fresh_events[0]["round_num"] == 2
    assert fresh_events[0]["every_n"] == 2


def test_given_no_fresh_eyes_config_when_rounds_run_then_no_trigger_events(
    tmp_path: Path,
):
    cfg_path = make_toml_with_sections(tmp_path, runtime_extra="restart_delay_s = 1\n")
    log_dir = tmp_path / "logs"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
            "--max-rounds",
            "3",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = read_events_for_current_month(log_dir)
    fresh_events = [e for e in events if e.get("event") == "fresh_eyes_round_triggered"]
    assert len(fresh_events) == 0
