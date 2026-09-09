from __future__ import annotations

import json
import os
import signal as _sig
import subprocess
import sys
from pathlib import Path

from tests._test_helpers import wait_for


def _write_toml(tmp_git_repo: Path, fake_agent: Path, *, round_timeout: int = 5) -> Path:
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
""")
    (tmp_git_repo / ".gitignore").write_text("logs/\n")
    subprocess.run(["git", "add", "."], cwd=tmp_git_repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture"],
        cwd=tmp_git_repo,
        check=True,
    )
    return toml


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
