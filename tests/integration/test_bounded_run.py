from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests._test_helpers import make_toml_with_sections, read_events_for_current_month

# Every test below spawns a REAL `agent-runner serve` subprocess (its own
# python interpreter startup, one `agent-runner round` sub-invocation per
# round, plus restart_delay_s sleeps) and bounds the WHOLE run with a fixed
# subprocess.run(timeout=...). Confirmed by reproduction: under `-n auto`
# combined with heavy host contention, that fixed bound can be exceeded even
# though nothing is actually wedged -- just genuinely slow interpreter
# startup/scheduling under contention. Each test below widens its bound with
# real headroom and pins its OWN `@pytest.mark.timeout` so that headroom
# doesn't eat into the suite's shared 60s per-test ceiling.


@pytest.mark.timeout(90)
def test_given_max_rounds_3_when_serve_runs_then_exits_after_3_rounds(tmp_path: Path):
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra="restart_delay_s = 1\n",
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
        timeout=60,  # widened from 20 -- see the file-header comment above
    )
    assert proc.returncode == 0
    events = read_events_for_current_month(log_dir)
    max_rounds_events = [e for e in events if e.get("event") == "max_rounds_reached"]
    assert len(max_rounds_events) == 1
    assert max_rounds_events[0]["rounds_completed"] == 3
    assert max_rounds_events[0]["max_rounds"] == 3


@pytest.mark.timeout(70)
def test_given_stop_file_touched_when_serve_runs_then_exits_with_event(tmp_path: Path):
    stop_file = tmp_path / "logs" / "stop-now"
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra=f'restart_delay_s = 1\nstop_file = "{stop_file}"\n',
    )
    log_dir = tmp_path / "logs"
    # Touch stop_file BEFORE serve so first between-rounds check catches it
    stop_file.write_text("stop test")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(cfg_path),
            "serve",
        ],
        capture_output=True,
        text=True,
        timeout=40,  # widened from 10 -- see the file-header comment above
    )
    assert proc.returncode == 0
    events = read_events_for_current_month(log_dir)
    stop_events = [e for e in events if e.get("event") == "stop_file_detected"]
    assert len(stop_events) == 1
    assert stop_events[0]["content"] == "stop test"
    assert stop_events[0]["stop_file"] == str(stop_file)


@pytest.mark.timeout(90)
def test_given_cli_max_rounds_overrides_config_value(tmp_path: Path):
    """CLI --max-rounds 2 overrides [runtime] max_rounds = 5."""
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra="restart_delay_s = 1\nmax_rounds = 5\n",
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
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=60,  # widened from 20 -- see the file-header comment above
    )
    assert proc.returncode == 0
    events = read_events_for_current_month(log_dir)
    max_events = [e for e in events if e.get("event") == "max_rounds_reached"]
    assert len(max_events) == 1
    assert max_events[0]["max_rounds"] == 2  # CLI value wins
    assert max_events[0]["rounds_completed"] == 2
