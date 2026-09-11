from __future__ import annotations

import json
import os
import signal as _sig
import subprocess
import sys
from pathlib import Path

from tests._test_helpers import wait_for


def _write_toml(
    tmp_git_repo: Path, fake_agent: Path, *, round_timeout: int = 5, phases_block: str = ""
) -> Path:
    toml = tmp_git_repo / "agent-runner.toml"
    prompt = tmp_git_repo / "p.md"
    prompt.write_text("Body content for serve loop test. " * 50)
    log_dir = tmp_git_repo / "logs"
    toml.write_text(f"""
[agent]
command = ["{fake_agent}"]
prompt_arg_template = ["{{prompt}}"]
[runtime]
work_dir = "{tmp_git_repo}"
log_dir = "{log_dir}"
round_timeout_s = {round_timeout}
restart_delay_s = 1
[prompt]
file = "{prompt}"
""" + phases_block)
    (tmp_git_repo / ".gitignore").write_text("logs/\n")
    subprocess.run(["git", "add", "."], cwd=tmp_git_repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture"],
        cwd=tmp_git_repo,
        check=True,
    )
    return toml


def _all_events(log_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for f in sorted(log_dir.glob("events-*.jsonl"))
        for line in f.read_text().splitlines()
        if line.strip()
    ]


def test_serve_once_should_run_one_round_and_exit_when_agent_succeeds(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    toml = _write_toml(tmp_git_repo, fake_agent_script)
    env = os.environ.copy()
    env["FAKE_AGENT_BEHAVIOR"] = "succeed"

    r = subprocess.run(
        [sys.executable, "-m", "agent_runner.cli", "--config", str(toml), "serve", "--once"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert r.returncode == 0
    status = json.loads((tmp_git_repo / "logs" / "status.json").read_text())
    assert status["round_num"] == 1


def test_serve_should_exit_after_current_round_when_sigterm_received(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    toml = _write_toml(tmp_git_repo, fake_agent_script, round_timeout=10)
    env = os.environ.copy()
    env["FAKE_AGENT_BEHAVIOR"] = "succeed"
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_runner.cli", "--config", str(toml), "serve"],
        env=env,
    )
    log_dir = tmp_git_repo / "logs"

    try:
        # Wait for serve to be up (not an event -- the pidfile write predates
        # the first round) rather than guessing a fixed delay: this is the
        # SIGTERM-before-round case, sending the signal before serve has
        # necessarily started its first round.
        assert wait_for(log_dir, lambda: (log_dir / "serve.pid").exists(), timeout_s=20), (
            "serve never wrote its pidfile"
        )
        proc.send_signal(_sig.SIGTERM)
        rc = proc.wait(timeout=20)
        assert rc == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_serve_should_emit_phase_window_overlap_once_when_two_rounds_run(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    # Two agent-overriding phases sharing an always-open window: a guaranteed
    # config-level collision. serve's own process (not a `round` child) owns the
    # boot-once detection, so the warning must appear exactly once regardless of
    # how many round subprocesses follow.
    phases_block = """
[phases]
list = ["a", "b"]
[phases.a.agent]
name = "phase-a"
[phases.a.schedule]
run_windows = ["00:00-24:00"]
[phases.b.agent]
name = "phase-b"
[phases.b.schedule]
run_windows = ["00:00-24:00"]
"""
    toml = _write_toml(tmp_git_repo, fake_agent_script, phases_block=phases_block)
    env = os.environ.copy()
    env["FAKE_AGENT_BEHAVIOR"] = "succeed"

    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_runner.cli",
            "--config",
            str(toml),
            "serve",
            "--max-rounds",
            "2",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert r.returncode == 0, r.stderr
    log_dir = tmp_git_repo / "logs"
    status = json.loads((log_dir / "status.json").read_text())
    overlaps = [e for e in _all_events(log_dir) if e["event"] == "phase_window_overlap"]
    assert status["round_num"] == 2  # two round subprocesses actually ran
    assert len(overlaps) == 1
    assert {overlaps[0]["phase_a"], overlaps[0]["phase_b"]} == {"a", "b"}
