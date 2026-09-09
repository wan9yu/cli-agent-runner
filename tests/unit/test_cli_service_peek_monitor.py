from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner.cli import main
from tests._test_helpers import make_toml


def _init(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo)
    main(["init", "--no-commit"])


def test_status_subcommand_should_call_api_status_when_invoked(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(tmp_git_repo, monkeypatch)
    with patch("agent_runner.api.status") as st:
        from agent_runner.api_types import ServiceMode, ServiceStatus

        st.return_value = ServiceStatus(mode=ServiceMode.NONE, active=False)

        rc = main(["status"])

        assert rc == 0
        st.assert_called_once()


def test_stop_subcommand_should_call_api_stop_when_invoked(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(tmp_git_repo, monkeypatch)
    with patch("agent_runner.api.stop") as stop:
        from agent_runner.api_types import ServiceMode, ServiceStatus

        stop.return_value = ServiceStatus(mode=ServiceMode.NONE, active=False)

        main(["stop"])

        stop.assert_called_once()


def test_kill_subcommand_should_call_api_kill_when_invoked(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(tmp_git_repo, monkeypatch)
    with patch("agent_runner.api.kill") as k:
        from agent_runner.api_types import ServiceMode, ServiceStatus

        k.return_value = ServiceStatus(mode=ServiceMode.NONE, active=False)

        main(["kill"])

        k.assert_called_once()


def test_cmd_restart_should_reject_cleanly_when_service_is_pid_file_based(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    """cmd_restart must catch the RuntimeError api.restart() raises for a
    non-systemd service and turn it into a clean `agent-runner: ...` stderr
    line + non-zero rc — not let it propagate as a raw traceback. The service
    must also not be touched (no SIGTERM/SIGKILL sent)."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    _init(tmp_git_repo, monkeypatch)
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


def test_peek_should_pass_select_arg_when_invoked_with_select(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    _init(tmp_git_repo, monkeypatch)
    with patch("agent_runner.api.peek", return_value=42) as p:
        rc = main(["peek", "--select", "system.disk_used_pct"])

        assert rc == 0
        kwargs = p.call_args.kwargs
        assert kwargs["select"] == "system.disk_used_pct"
        out = capsys.readouterr().out
        assert "42" in out


@pytest.mark.parametrize("mode", ["anomaly", "narrate", "http"])
def test_monitor_cmd_should_reject_host_when_mode_not_events(
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


def test_monitor_cmd_should_reject_kind_flag_when_host_not_set(capsys, tmp_path: Path) -> None:
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


def test_cmd_stop_should_print_stopping_and_stopped_to_stderr_when_not_json(
    monkeypatch, capsys, tmp_path
) -> None:
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


def test_cmd_stop_should_be_silent_on_stderr_when_json_mode(monkeypatch, capsys, tmp_path) -> None:
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


def test_monitor_should_reject_host_when_mode_is_narrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_monitor_cmd_should_default_to_anomaly_mode_when_mode_omitted(
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


def test_main_should_dispatch_events_stream_when_mode_is_events(
    monkeypatch, tmp_path: Path
) -> None:
    from agent_runner import api

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


def test_main_should_dispatch_relay_when_mode_events_with_host(monkeypatch, tmp_path: Path) -> None:
    """`monitor --mode events --host pi` is the supported remote combination."""
    from agent_runner import api
    from agent_runner.cli import monitor_cmd

    cfg_path = make_toml(tmp_path)
    seen: dict = {}

    def fake_relay(host, **kwargs):
        seen["host"] = host
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(api, "relay_remote_events", fake_relay)
    # monitor_cmd installs the relay's real SIGTERM handler on this path;
    # this test drives the real CLI in-process, so neutralize it --
    # signal.signal is a raw OS call monkeypatch cannot auto-revert, and
    # leaving the real handler armed would permanently rewire this pytest
    # worker's SIGTERM disposition for the rest of the session.
    monkeypatch.setattr(monitor_cmd, "_install_term_handler", lambda: None)

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


def test_main_should_default_relay_kinds_to_none_when_kind_omitted(
    monkeypatch, tmp_path: Path
) -> None:
    """Omitting --kind hands the relay None, which resolves to every known kind."""
    from agent_runner import api
    from agent_runner.cli import monitor_cmd

    cfg_path = make_toml(tmp_path)
    seen: dict = {}

    monkeypatch.setattr(api, "relay_remote_events", lambda host, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(monitor_cmd, "_install_term_handler", lambda: None)

    rc = main(["--config", str(cfg_path), "monitor", "--mode", "events", "--host", "pi"])

    assert rc == 0
    assert seen["kinds"] is None
    assert seen["remote_config"] is None
