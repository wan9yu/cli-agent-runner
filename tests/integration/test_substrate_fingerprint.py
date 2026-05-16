"""Integration tests for substrate fingerprint events emitted by serve loop."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from tests._test_helpers import make_toml_with_sections


def _read_events(log_dir: Path) -> list[dict]:
    month = datetime.now(UTC).strftime("%Y-%m")
    events_path = log_dir / f"events-{month}.jsonl"
    return [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]


def test_given_round_runs_when_substrate_emitted_then_before_and_after_present(
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
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = _read_events(log_dir)
    before_events = [e for e in events if e.get("event") == "round_substrate_before"]
    after_events = [e for e in events if e.get("event") == "round_substrate_after"]
    assert len(before_events) == 1
    assert len(after_events) == 1
    # Without git or paths config: both null
    assert before_events[0]["git_head"] is None
    assert before_events[0]["paths_hash"] is None


def test_given_paths_config_when_round_runs_then_paths_hash_populated(tmp_path: Path):
    # Create a file to hash
    (tmp_path / "tracked.py").write_text("x = 1\n")
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra=('restart_delay_s = 1\nsubstrate_fingerprint_paths = ["*.py"]\n'),
    )
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
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = _read_events(log_dir)
    before_events = [e for e in events if e.get("event") == "round_substrate_before"]
    assert len(before_events) == 1
    assert before_events[0]["paths_hash"] is not None
    assert len(before_events[0]["paths_hash"]) == 64  # sha256 hex


def test_given_git_repo_when_round_runs_then_git_head_populated(tmp_path: Path):
    # Init a git repo in work_dir
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-m",
            "init",
            "-q",
        ],
        cwd=tmp_path,
        check=True,
    )
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
            "1",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = _read_events(log_dir)
    before_events = [e for e in events if e.get("event") == "round_substrate_before"]
    assert len(before_events) == 1
    assert before_events[0]["git_head"] is not None
    assert len(before_events[0]["git_head"]) >= 7  # SHA prefix at minimum
