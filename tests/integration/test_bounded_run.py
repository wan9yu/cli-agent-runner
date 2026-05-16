from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _make_test_toml(work_dir: Path, runtime_extra: str = "") -> Path:
    """Minimal TOML with a fake agent that exits instantly."""
    (work_dir / "p.md").write_text("hi")
    cfg_path = work_dir / "agent-runner.toml"
    cfg_path.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{work_dir}"\n'
        f'log_dir = "{work_dir}/logs"\n'
        "restart_delay_s = 1\n"
        f"{runtime_extra}"
        "[prompt]\n"
        f'file = "{work_dir}/p.md"\n'
    )
    (work_dir / "logs").mkdir()
    return cfg_path


def test_given_max_rounds_3_when_serve_runs_then_exits_after_3_rounds(tmp_path: Path):
    cfg_path = _make_test_toml(tmp_path)
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
        timeout=60,
    )
    assert proc.returncode == 0
    from datetime import UTC, datetime

    month = datetime.now(UTC).strftime("%Y-%m")
    events_path = log_dir / f"events-{month}.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line]
    max_rounds_events = [e for e in events if e.get("event") == "max_rounds_reached"]
    assert len(max_rounds_events) == 1
    assert max_rounds_events[0]["rounds_completed"] == 3
    assert max_rounds_events[0]["max_rounds"] == 3


def test_given_stop_file_touched_when_serve_runs_then_exits_with_event(tmp_path: Path):
    stop_file = tmp_path / "logs" / "stop-now"
    cfg_path = _make_test_toml(
        tmp_path, runtime_extra=f'stop_file = "{stop_file}"\n'
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
        timeout=10,
    )
    assert proc.returncode == 0
    from datetime import UTC, datetime

    month = datetime.now(UTC).strftime("%Y-%m")
    events_path = log_dir / f"events-{month}.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line]
    stop_events = [e for e in events if e.get("event") == "stop_file_detected"]
    assert len(stop_events) == 1
    assert stop_events[0]["content"] == "stop test"
    assert stop_events[0]["stop_file"] == str(stop_file)


def test_given_cli_max_rounds_overrides_config_value(tmp_path: Path):
    """CLI --max-rounds 2 overrides [runtime] max_rounds = 5."""
    cfg_path = _make_test_toml(tmp_path, runtime_extra="max_rounds = 5\n")
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
        timeout=60,
    )
    assert proc.returncode == 0
    from datetime import UTC, datetime

    month = datetime.now(UTC).strftime("%Y-%m")
    events_path = log_dir / f"events-{month}.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line]
    max_events = [e for e in events if e.get("event") == "max_rounds_reached"]
    assert len(max_events) == 1
    assert max_events[0]["max_rounds"] == 2  # CLI value wins
    assert max_events[0]["rounds_completed"] == 2
