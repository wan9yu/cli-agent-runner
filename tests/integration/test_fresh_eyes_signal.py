"""Integration tests for fresh_eyes signal injection in the serve loop."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._test_helpers import (
    FakeArgs,
    make_toml_with_sections,
    read_events_for_current_month,
)


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


def _capturing_run(captured: list[dict]):
    """subprocess.run stand-in: records the ROUND subprocess env, passes
    everything else (git rev-parse, etc.) a benign rc=0 with .stdout so
    _substrate.compute_git_head doesn't take its AttributeError fallback."""

    def run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if isinstance(cmd, (list, tuple)) and "round" in cmd:
            captured.append(dict(kwargs["env"]))
        return type("R", (), {"returncode": 0, "stdout": ""})()

    return run


def test_given_fresh_eyes_round_when_serve_dispatches_then_env_var_is_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The round subprocess env carries AGENT_RUNNER_FRESH_EYES="1" on a trigger
    round and "0" otherwise. serve_cmd.py's injection line had zero coverage —
    the whole line could be deleted with the suite green, while the
    fresh_eyes_round_triggered event kept claiming the feature worked.
    """
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra="restart_delay_s = 1\nfresh_eyes_every_n = 2\n",
        vcs_block='[vcs]\ndirty_action = "ignore"\n',
    )
    captured: list[dict] = []
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(subprocess, "run", _capturing_run(captured))

    serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=2))

    assert len(captured) == 2, f"expected 2 dispatched rounds, got {len(captured)}"
    # Round 1 is not a multiple of 2; round 2 is.
    assert captured[0]["AGENT_RUNNER_FRESH_EYES"] == "0"
    assert captured[1]["AGENT_RUNNER_FRESH_EYES"] == "1"


def test_given_no_fresh_eyes_config_when_serve_dispatches_then_env_var_is_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The var is always defined (matching the AGENT_RUNNER_PHASE pattern) —
    pin that clause: with no fresh-eyes config it is present and "0"."""
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml_with_sections(tmp_path, vcs_block='[vcs]\ndirty_action = "ignore"\n')
    captured: list[dict] = []
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(subprocess, "run", _capturing_run(captured))

    serve_cmd.cmd(FakeArgs(cfg_path))  # once=True → exactly one round

    assert captured, "serve never dispatched a round"
    assert captured[0]["AGENT_RUNNER_FRESH_EYES"] == "0"
