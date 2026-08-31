from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner.cli import main
from tests._test_helpers import make_toml


def _init(repo: Path) -> None:
    os.chdir(repo)
    main(["init", "--no-commit"])


def test_given_status_subcommand_when_invoked_then_calls_api_status(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(tmp_git_repo)
    with patch("agent_runner.api.status") as st:
        from agent_runner.api_types import ServiceMode, ServiceStatus

        st.return_value = ServiceStatus(mode=ServiceMode.NONE, active=False)
        rc = main(["status"])
        assert rc == 0
        st.assert_called_once()


def test_given_stop_subcommand_when_invoked_then_calls_api_stop(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(tmp_git_repo)
    with patch("agent_runner.api.stop") as stop:
        from agent_runner.api_types import ServiceMode, ServiceStatus

        stop.return_value = ServiceStatus(mode=ServiceMode.NONE, active=False)
        main(["stop"])
        stop.assert_called_once()


def test_given_kill_subcommand_when_invoked_then_calls_api_kill(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(tmp_git_repo)
    with patch("agent_runner.api.kill") as k:
        from agent_runner.api_types import ServiceMode, ServiceStatus

        k.return_value = ServiceStatus(mode=ServiceMode.NONE, active=False)
        main(["kill"])
        k.assert_called_once()


def test_given_pid_file_service_when_restart_then_clean_error_not_traceback(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    """cmd_restart must catch the RuntimeError api.restart() raises for a
    non-systemd service and turn it into a clean `agent-runner: ...` stderr
    line + non-zero rc — not let it propagate as a raw traceback. The service
    must also not be touched (no SIGTERM/SIGKILL sent)."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    _init(tmp_git_repo)
    from agent_runner.config import load_config

    log_dir = load_config(tmp_git_repo / "agent-runner.toml").runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serve.pid").write_text("12345")

    with patch("agent_runner.api.send_signal_to_pid", return_value=True) as send:
        rc = main(["restart"])

    send.assert_not_called()  # service not stopped
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("agent-runner: ")
    assert "systemd" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_given_peek_with_select_when_invoked_then_passes_select_arg(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _init(tmp_git_repo)
    with patch("agent_runner.api.peek", return_value=42) as p:
        rc = main(["peek", "--select", "system.disk_used_pct"])
        assert rc == 0
        kwargs = p.call_args.kwargs
        assert kwargs["select"] == "system.disk_used_pct"
        out = capsys.readouterr().out
        assert "42" in out


@pytest.mark.parametrize("mode", ["anomaly", "narrate", "http"])
def test_given_host_with_detection_mode_when_cmd_then_exit_1_pointing_at_events(
    capsys, tmp_path: Path, mode: str
) -> None:
    """--host is rejected for every mode but events: detection runs on-host."""
    from types import SimpleNamespace

    from agent_runner.cli import monitor_cmd

    # No config file needed: the gate fires before anything is loaded.
    args = SimpleNamespace(
        host="pi",
        interval=None,
        mode=mode,
        kind=None,
        remote_config=None,
        port=8765,
        json=False,
        config=str(tmp_path / "agent-runner.toml"),
    )
    rc = monitor_cmd.cmd(args)

    captured = capsys.readouterr()
    assert rc == 1
    assert f"remote monitoring (--host pi) is unsupported for --mode {mode}" in captured.err
    assert "detection runs on the supervised host by design" in captured.err
    assert "monitor --host pi --mode events" in captured.err
    assert "docs/runbook.md" in captured.err
    assert captured.out == ""  # error path must not leak to stdout


def test_given_relay_only_flags_without_host_when_cmd_then_rejected(capsys, tmp_path: Path) -> None:
    """--kind / --remote-config must not silently no-op in local mode."""
    from types import SimpleNamespace

    from agent_runner.cli import monitor_cmd

    args = SimpleNamespace(
        host=None,
        interval=None,
        mode="events",
        kind="round_end",
        remote_config=None,
        port=8765,
        json=False,
        config=str(tmp_path / "agent-runner.toml"),
    )
    assert monitor_cmd.cmd(args) == 1
    assert "--kind applies to --host --mode events only" in capsys.readouterr().err


def test_given_cmd_stop_when_not_json_then_prints_stopping_and_stopped_to_stderr(
    monkeypatch, capsys, tmp_path
) -> None:
    """Non-json mode prints two stderr lines around api.stop()."""
    from types import SimpleNamespace

    from agent_runner import api
    from agent_runner.cli import service_cmd

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / "agent-runner.toml").write_text(
        f'[agent]\ncommand = ["true"]\n[runtime]\nwork_dir = "{work_dir}"\n[prompt]\ninline = "p"\n'
    )

    def fake_stop(_wd):
        return {"stopped": True}

    monkeypatch.setattr(api, "stop", fake_stop)

    args = SimpleNamespace(json=False, config=str(work_dir / "agent-runner.toml"))
    rc = service_cmd.cmd_stop(args)

    captured = capsys.readouterr()
    assert rc == 0
    assert "agent-runner: stopping service..." in captured.err
    assert "agent-runner: stopped (" in captured.err
    assert "s)" in captured.err


def test_given_cmd_stop_when_json_mode_then_stderr_silent(monkeypatch, capsys, tmp_path) -> None:
    """Json mode is silent on stderr — machine readers want clean stdout JSON only."""
    from types import SimpleNamespace

    from agent_runner import api
    from agent_runner.cli import service_cmd

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / "agent-runner.toml").write_text(
        f'[agent]\ncommand = ["true"]\n[runtime]\nwork_dir = "{work_dir}"\n[prompt]\ninline = "p"\n'
    )

    monkeypatch.setattr(api, "stop", lambda _wd: {"stopped": True})

    args = SimpleNamespace(json=True, config=str(work_dir / "agent-runner.toml"))
    rc = service_cmd.cmd_stop(args)

    captured = capsys.readouterr()
    assert rc == 0
    assert "stopping" not in captured.err
    assert "stopped" not in captured.err


def test_given_monitor_mode_narrate_when_with_host_then_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--mode narrate is local-only; --host is incompatible."""
    from agent_runner.cli import main

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    rc = main(
        [
            "--config",
            str(tmp_path / "agent-runner.toml"),
            "monitor",
            "--mode",
            "narrate",
            "--host",
            "pi",
        ]
    )
    assert rc == 1


def test_given_monitor_no_mode_when_invoked_then_anomaly_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default --mode is anomaly (preserves existing behavior)."""
    from agent_runner.cli import monitor_cmd

    captured = {}

    def fake_monitor_loop(*args, **kwargs):
        captured["called"] = True
        return iter([])

    monkeypatch.setattr("agent_runner.api.monitor_loop", fake_monitor_loop)

    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
    )

    from types import SimpleNamespace

    args = SimpleNamespace(
        host=None,
        interval=None,
        mode="anomaly",
        json=False,
        config=str(tmp_path / "agent-runner.toml"),
    )

    rc = monitor_cmd.cmd(args)
    assert rc == 0
    assert captured.get("called"), "anomaly mode should call monitor_loop"


def test_given_mode_events_when_main_then_dispatches_events_stream(
    monkeypatch, tmp_path: Path
) -> None:
    """`monitor --mode events` calls api.stream_events_jsonl and prints JSONL."""
    from agent_runner import api
    from agent_runner.cli import main

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    events_seen = []
    captured_log_dir = {}

    def fake_stream(log_dir_arg, **_kwargs):
        captured_log_dir["path"] = log_dir_arg
        for evt in [{"event": "round_start", "round_num": 1}]:
            events_seen.append(evt)
            yield evt

    monkeypatch.setattr(api, "stream_events_jsonl", fake_stream)

    rc = main(["--config", str(cfg_path), "monitor", "--mode", "events"])
    assert rc == 0
    assert captured_log_dir["path"] == log_dir
    assert len(events_seen) == 1


def test_given_mode_events_with_host_when_main_then_dispatches_relay(
    monkeypatch, tmp_path: Path
) -> None:
    """`monitor --mode events --host pi` is the supported remote combination."""
    from agent_runner import api
    from agent_runner.cli import main

    cfg_path = make_toml(tmp_path)
    seen: dict = {}

    def fake_relay(host, **kwargs):
        seen["host"] = host
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(api, "relay_remote_events", fake_relay)

    rc = main(
        [
            "--config",
            str(cfg_path),
            "monitor",
            "--mode",
            "events",
            "--host",
            "pi",
            "--kind",
            "round_end, oauth_fail",
            "--remote-config",
            "/srv/proj/agent-runner.toml",
        ]
    )
    assert rc == 0
    assert seen["host"] == "pi"
    assert seen["kinds"] == ["round_end", "oauth_fail"]
    assert seen["remote_config"] == "/srv/proj/agent-runner.toml"
    assert seen["log_dir"] == tmp_path / "logs", "blips land in the CLIENT's log dir"
    assert seen["failure_tolerance_s"] == 90, "default [monitor] remote_failure_tolerance_s"


def test_given_mode_events_with_host_and_no_kind_when_main_then_relay_defaults(
    monkeypatch, tmp_path: Path
) -> None:
    """Omitting --kind hands the relay None, which resolves to every known kind."""
    from agent_runner import api
    from agent_runner.cli import main

    cfg_path = make_toml(tmp_path)
    seen: dict = {}

    monkeypatch.setattr(api, "relay_remote_events", lambda host, **kw: seen.update(kw) or 0)

    rc = main(["--config", str(cfg_path), "monitor", "--mode", "events", "--host", "pi"])
    assert rc == 0
    assert seen["kinds"] is None
    assert seen["remote_config"] is None
