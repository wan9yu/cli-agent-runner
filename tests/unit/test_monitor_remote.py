from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_runner.api_types import Alert
from agent_runner.monitor import (
    RemoteSource,
    on_alert,
    run_remote_command,
)


def test_given_run_remote_command_when_called_then_invokes_ssh() -> None:
    with patch("agent_runner.monitor.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        rc, out = run_remote_command("pi", "echo ok")
        assert (rc, out) == (0, "ok\n")
        argv = run.call_args[0][0]
        assert argv[0] == "ssh"
        assert argv[1] == "pi"


def test_given_remote_source_when_files_listed_then_returns_remote_paths() -> None:
    with patch("agent_runner.monitor.run_remote_command") as rc:
        rc.return_value = (0, "events-2026-05.jsonl\nmetrics-2026-05.jsonl\n")
        src = RemoteSource(host="pi", project="myproj")
        files = src.events_files()
        assert any("events-2026-05.jsonl" in str(p) for p in files)


def test_given_alert_with_no_auto_action_when_on_alert_then_does_nothing() -> None:
    a = Alert(severity="warning", detector="timeout_rate", message="m",
              context={}, ts="t", auto_action="none")
    with patch("agent_runner.monitor.subprocess.run") as run:
        on_alert(a, project="myproj", host=None, log_dir=Path("/tmp/fake"))
        run.assert_not_called()


def test_given_critical_alert_local_when_on_alert_then_calls_local_stop(tmp_log_dir: Path) -> None:
    a = Alert(severity="critical", detector="oauth_fail", message="m",
              context={}, ts="t", auto_action="stop_service")
    with patch("agent_runner.monitor._call_local_stop") as stop:
        on_alert(a, project="myproj", host=None, log_dir=tmp_log_dir)
        stop.assert_called_once_with("myproj")


def test_given_critical_alert_remote_when_on_alert_then_calls_ssh_stop(tmp_log_dir: Path) -> None:
    a = Alert(severity="critical", detector="oauth_fail", message="m",
              context={}, ts="t", auto_action="stop_service")
    with patch("agent_runner.monitor.run_remote_command") as rc:
        rc.return_value = (0, "")
        on_alert(a, project="myproj", host="pi", log_dir=tmp_log_dir)
        rc.assert_called_once()
        cmd = rc.call_args[0][1]
        assert "agent-runner stop" in cmd
