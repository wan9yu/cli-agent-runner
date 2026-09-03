"""Lifecycle-safety no-op closure — the missing e2e coverage (Group B).

Before this task: `api.stop`/`api.kill` no-op modes recorded success in the
event log while the runaway process kept running (or was never reached at
all). These tests exercise the REAL verbs against REAL processes, not mocks.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from agent_runner import api
from agent_runner.api_types import Alert
from agent_runner.lifecycle import PIDFile, pid_alive
from agent_runner.monitor import on_alert
from tests._test_helpers import read_events_for_current_month


def _write_toml(
    tmp_git_repo: Path, fake_agent: Path, *, round_timeout: int = 10, restart_delay: int = 1
) -> Path:
    toml = tmp_git_repo / "agent-runner.toml"
    prompt = tmp_git_repo / "p.md"
    prompt.write_text("Body content for the auto-stop e2e test. " * 50)
    log_dir = tmp_git_repo / "logs"
    toml.write_text(f"""
[agent]
command = ["{fake_agent}"]
prompt_arg_template = ["{{prompt}}"]
[runtime]
work_dir = "{tmp_git_repo}"
log_dir = "{log_dir}"
round_timeout_s = {round_timeout}
restart_delay_s = {restart_delay}
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


def _reap_in_background(proc: subprocess.Popen) -> None:
    """Block-wait for ``proc`` on a daemon thread so the OS reaps it the
    INSTANT it exits, rather than leaving a zombie until the test's own
    assertions get around to calling .wait()/.poll(). Without this, this
    test process (the real parent of ``proc``) would make every
    ``pid_alive(proc.pid)`` check inside api.stop/api.kill see the zombie's
    still-occupied pid as "alive" for as long as the test keeps it unreaped —
    a test-harness artifact that production code never hits (there, serve's
    actual parent reaps it independently of the separate `agent-runner kill`
    CLI process checking liveness)."""
    threading.Thread(target=proc.wait, daemon=True).start()


def _wait_for(predicate, *, timeout_s: float, interval_s: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def test_alert_drives_real_serve_to_stop(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A critical alert -> on_alert -> api.stop must actually stop a REAL,
    hand-launched (PID_FILE mode) serve, and must emit
    monitor_auto_stop_triggered ONLY once the stop is confirmed -- never
    monitor_auto_stop_failed for a stop that genuinely worked."""
    # Keep detect_service_mode away from any real ~/.config/systemd/user unit
    # on the dev/CI box this test happens to run on.
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: tmp_git_repo / "nou")
    # The monitor loop hands on_alert the work_dir Path (api._monitor_loop_iter),
    # so api.stop resolves the project's real (non-preset) log_dir regardless of
    # cwd. Run from a FOREIGN cwd to prove that: a bare-name resolution here would
    # miss the pidfile and no-op while serve kept running.
    foreign = tmp_git_repo / "elsewhere"
    foreign.mkdir()
    monkeypatch.chdir(foreign)

    toml = _write_toml(tmp_git_repo, fake_agent_script)
    log_dir = tmp_git_repo / "logs"
    env = os.environ.copy()
    env["FAKE_AGENT_BEHAVIOR"] = "succeed"
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_runner.cli", "--config", str(toml), "serve"],
        env=env,
    )
    _reap_in_background(proc)
    try:
        assert _wait_for((log_dir / "serve.pid").exists, timeout_s=20), (
            "serve never wrote its pidfile"
        )

        alert = Alert(
            severity="critical",
            detector="oauth_fail",
            message="auth failure detected",
            context={},
            ts="2026-01-01T00:00:00.000Z",
            auto_action="stop_service",
        )
        on_alert(
            alert,
            project=tmp_git_repo,
            log_dir=log_dir,
            allowed_stop_names=["oauth_fail"],
        )

        assert _wait_for(lambda: not api.status(tmp_git_repo).active, timeout_s=20), (
            "serve was not stopped by the auto-stop alert"
        )
        assert _wait_for(lambda: proc.poll() is not None, timeout_s=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    kinds = [e["event"] for e in read_events_for_current_month(log_dir)]
    assert "monitor_auto_stop_triggered" in kinds
    assert "monitor_auto_stop_failed" not in kinds


def test_kill_reaps_round_and_agent_pgroup_via_holder_sidecar(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api.kill's PID_FILE mode cannot reach an in-flight round or its agent via
    killpg -- both are start_new_session=True, outside whatever process group
    the serve pid belongs to. It must read the round's own pid from the lock's
    .holder sidecar and TERM-first it directly, so the round's own SIGTERM
    handler reaps its agent pgroup. This drives that whole chain against REAL
    processes and asserts the round, its agent, AND the (stand-in) serve
    process are all gone afterward -- not left running with the event log
    claiming success."""
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: tmp_git_repo / "nou")

    toml = _write_toml(tmp_git_repo, fake_agent_script, round_timeout=60)
    log_dir = tmp_git_repo / "logs"
    env = os.environ.copy()
    env["FAKE_AGENT_BEHAVIOR"] = "hang"

    round_proc = subprocess.Popen(
        [sys.executable, "-m", "agent_runner.cli", "--config", str(toml), "round"],
        env=env,
    )
    serve_stub = subprocess.Popen(["sleep", "60"])
    _reap_in_background(round_proc)
    _reap_in_background(serve_stub)
    try:
        holder = log_dir / "agent-runner.lock.holder"
        assert _wait_for(holder.exists, timeout_s=15), "round never wrote its lock holder sidecar"

        round_ps = psutil.Process(round_proc.pid)
        agent_pid: list[int] = []

        def _agent_spawned() -> bool:
            children = round_ps.children()
            if children:
                agent_pid.append(children[0].pid)
                return True
            return False

        assert _wait_for(_agent_spawned, timeout_s=15), "round never spawned its hanging agent"

        PIDFile(log_dir / "serve.pid").write(serve_stub.pid)

        result = api.kill(tmp_git_repo)

        assert result.active is False
        assert _wait_for(lambda: round_proc.poll() is not None, timeout_s=20), (
            "the round process was left running"
        )
        assert not pid_alive(agent_pid[0]), "the round's agent was orphaned by kill()"
        assert serve_stub.poll() is not None  # the serve stand-in was reaped too
    finally:
        for p in (round_proc, serve_stub):
            if p.poll() is None:
                p.kill()
                p.wait()
