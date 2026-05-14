from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner.cli import main


def _make_toml(tmp_path: Path) -> Path:
    """Write a minimal agent-runner.toml and return its path."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )
    return toml


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


def test_given_cancel_subcommand_when_invoked_then_calls_api_cancel(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(tmp_git_repo)
    with patch("agent_runner.api.cancel", return_value=True) as c:
        main(["cancel"])
        c.assert_called_once()


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


def test_given_monitor_with_host_when_invoked_then_passes_host_arg(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init(tmp_git_repo)
    with patch("agent_runner.api.monitor_loop") as ml:
        ml.return_value = iter([])
        main(["monitor", "--host", "pi", "--interval", "1"])
        kwargs = ml.call_args.kwargs
        assert kwargs["host"] == "pi"
        assert kwargs["interval_s"] == 1


def test_given_monitor_loop_raises_remote_error_when_cmd_then_stderr_and_exit_1(
    monkeypatch, capsys, tmp_path
) -> None:
    """CLI surfaces MonitorRemoteError as stderr line + exit 1."""
    from types import SimpleNamespace

    from agent_runner import monitor
    from agent_runner.cli import monitor_cmd

    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    (work_dir / "agent-runner.toml").write_text(
        f'[agent]\ncommand = ["true"]\n[runtime]\nwork_dir = "{work_dir}"\n[prompt]\ninline = "p"\n'
    )

    ssh_err = "ssh: connect to host pi port 22: Connection refused"

    def fake_monitor_loop(*_args, **_kwargs):
        raise monitor.MonitorRemoteError("pi", ssh_err)
        yield  # pragma: no cover — makes this a generator function

    from agent_runner import api

    monkeypatch.setattr(api, "monitor_loop", fake_monitor_loop)

    cfg = str(work_dir / "agent-runner.toml")
    args = SimpleNamespace(host="pi", interval=None, json=False, config=cfg)
    rc = monitor_cmd.cmd(args)

    captured = capsys.readouterr()
    assert rc == 1
    assert "cannot reach 'pi'" in captured.err
    assert "Connection refused" in captured.err
    assert captured.out == ""  # error path must not leak to stdout


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

    cfg_path = _make_toml(tmp_path)
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


def test_given_mode_events_with_host_when_main_then_error(monkeypatch, tmp_path: Path) -> None:
    """`monitor --mode events --host pi` rejected (local-only, like narrate)."""
    from agent_runner.cli import main

    cfg_path = _make_toml(tmp_path)

    rc = main(["--config", str(cfg_path), "monitor", "--mode", "events", "--host", "pi"])
    assert rc != 0
