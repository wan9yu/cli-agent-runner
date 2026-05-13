from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    a = Alert(
        severity="warning",
        detector="timeout_rate",
        message="m",
        context={},
        ts="t",
        auto_action="none",
    )
    with patch("agent_runner.monitor.subprocess.run") as run:
        on_alert(a, project="myproj", host=None, log_dir=Path("/tmp/fake"))
        run.assert_not_called()


def test_given_critical_alert_local_when_on_alert_then_calls_local_stop(tmp_log_dir: Path) -> None:
    a = Alert(
        severity="critical",
        detector="oauth_fail",
        message="m",
        context={},
        ts="t",
        auto_action="stop_service",
    )
    with patch("agent_runner.monitor._call_local_stop") as stop:
        on_alert(a, project="myproj", host=None, log_dir=tmp_log_dir)
        stop.assert_called_once_with("myproj")


def test_given_critical_alert_remote_when_on_alert_then_calls_ssh_stop(tmp_log_dir: Path) -> None:
    a = Alert(
        severity="critical",
        detector="oauth_fail",
        message="m",
        context={},
        ts="t",
        auto_action="stop_service",
    )
    with patch("agent_runner.monitor.run_remote_command") as rc:
        rc.return_value = (0, "")
        on_alert(a, project="myproj", host="pi", log_dir=tmp_log_dir)
        rc.assert_called_once()
        cmd = rc.call_args[0][1]
        assert "agent-runner stop" in cmd


def test_given_ssh_rc_255_when_run_remote_command_then_raises_monitor_remote_error(
    monkeypatch,
) -> None:
    """ssh exits 255 on protocol-level failure (host unreachable, key reject)."""
    import subprocess

    from agent_runner.monitor import MonitorRemoteError, run_remote_command

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=_args[0],
            returncode=255,
            stdout="",
            stderr="ssh: connect to host pi port 22: Connection refused\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(MonitorRemoteError) as exc_info:
        run_remote_command("pi", "ls /tmp")
    assert exc_info.value.host == "pi"
    assert "Connection refused" in exc_info.value.stderr


def test_given_ssh_rc_2_when_run_remote_command_then_returned_tolerated(
    monkeypatch,
) -> None:
    """ssh rc=2 (command 'no match') is legitimate — caller decides."""
    import subprocess

    from agent_runner.monitor import run_remote_command

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=_args[0], returncode=2, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    rc, out = run_remote_command("pi", "ls /nope")
    assert rc == 2
    assert out == ""


def test_given_host_starts_with_dash_when_run_remote_command_then_raises_value_error() -> None:
    """Reject ssh hosts beginning with '-' to prevent ProxyCommand injection."""
    from agent_runner.monitor import run_remote_command

    with pytest.raises(ValueError, match="starts with '-'"):
        run_remote_command("-oProxyCommand=touch /tmp/x", "ls")


def test_given_remote_auto_stop_raises_when_on_alert_then_emits_failed_event(
    monkeypatch, tmp_path
) -> None:
    """on_alert tolerates MonitorRemoteError from remote stop and emits event."""
    import json

    from agent_runner.api_types import Alert
    from agent_runner.monitor import MonitorRemoteError, on_alert

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    def fake_run(*_args, **_kwargs):
        raise MonitorRemoteError("pi", "ssh: connect: Connection refused")

    # Patch run_remote_command via monitor module's reference
    from agent_runner import monitor

    monkeypatch.setattr(monitor, "run_remote_command", fake_run)

    alert = Alert(
        severity="critical",
        detector="oauth_fail",
        message="boom",
        context={},
        ts="2026-05-13T12:00:00.000Z",
        auto_action="stop_service",
    )

    # Should NOT raise — failure is logged as event instead
    on_alert(alert, project="proj", host="pi", log_dir=log_dir)

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    assert events_files
    all_events = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    failed = [e for e in all_events if e.get("event") == "monitor_auto_stop_failed"]
    assert failed, f"expected monitor_auto_stop_failed; got {[e.get('event') for e in all_events]}"
    assert failed[0]["host"] == "pi"
    assert "Connection refused" in failed[0]["error"]
