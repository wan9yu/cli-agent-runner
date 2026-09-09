"""Integration tests for substrate fingerprint events emitted by serve loop."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests._test_helpers import make_toml_with_sections, read_events_for_current_month


def _run_serve_one_round(cfg_path: Path) -> subprocess.CompletedProcess[str]:
    """Spawn the real CLI's ``serve --max-rounds 1`` and return the finished process."""
    return subprocess.run(
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
        env={**os.environ, "AGENT_RUNNER_SKIP_STARTUP_CHECK": "1"},
        timeout=20,
    )


def test_serve_should_emit_substrate_before_and_after_events_when_round_runs(
    tmp_path: Path,
):
    cfg_path = make_toml_with_sections(tmp_path, runtime_extra="restart_delay_s = 1\n")
    log_dir = tmp_path / "logs"

    proc = _run_serve_one_round(cfg_path)

    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = read_events_for_current_month(log_dir)
    before_events = [e for e in events if e.get("event") == "round_substrate_before"]
    after_events = [e for e in events if e.get("event") == "round_substrate_after"]
    assert len(before_events) == 1
    assert len(after_events) == 1
    # Without git or paths config: both null
    assert before_events[0]["git_head"] is None
    assert before_events[0]["paths_hash"] is None


def test_serve_should_populate_paths_hash_when_substrate_fingerprint_paths_configured(
    tmp_path: Path,
):
    # Create a file to hash
    (tmp_path / "tracked.py").write_text("x = 1\n")
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra=('restart_delay_s = 1\nsubstrate_fingerprint_paths = ["*.py"]\n'),
    )
    log_dir = tmp_path / "logs"

    proc = _run_serve_one_round(cfg_path)

    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = read_events_for_current_month(log_dir)
    before_events = [e for e in events if e.get("event") == "round_substrate_before"]
    assert len(before_events) == 1
    assert before_events[0]["paths_hash"] is not None
    assert len(before_events[0]["paths_hash"]) == 64  # sha256 hex


def test_serve_should_populate_git_head_when_work_dir_is_git_repo(tmp_path: Path):
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
    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra="restart_delay_s = 1\n",
        vcs_block='[vcs]\ndirty_action = "ignore"\n',
    )
    log_dir = tmp_path / "logs"

    proc = _run_serve_one_round(cfg_path)

    assert proc.returncode == 0, f"stderr={proc.stderr[:500]}"
    events = read_events_for_current_month(log_dir)
    before_events = [e for e in events if e.get("event") == "round_substrate_before"]
    assert len(before_events) == 1
    assert before_events[0]["git_head"] is not None
    assert len(before_events[0]["git_head"]) >= 7  # SHA prefix at minimum
