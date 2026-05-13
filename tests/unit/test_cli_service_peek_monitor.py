from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner.cli import main


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
