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
