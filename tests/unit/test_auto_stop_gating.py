"""Tests for ``on_alert``'s strict auto-stop gating via ``allowed_stop_names``."""

from __future__ import annotations

import re
import signal
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import api
from agent_runner.api_types import Alert
from agent_runner.monitor import on_alert


def _make_alert(detector: str, auto_action: str = "stop_service") -> Alert:
    return Alert(
        severity="critical",
        detector=detector,
        message="m",
        context={},
        ts="2026-01-01T00:00:00.000Z",
        auto_action=auto_action,
    )


def test_given_builtin_critical_in_allowed_list_when_on_alert_then_stop_called(
    tmp_path: Path,
) -> None:
    alert = _make_alert("oauth_fail")
    with patch("agent_runner.monitor._call_local_stop") as mock_stop:
        on_alert(
            alert,
            project="proj",
            log_dir=tmp_path,
            allowed_stop_names=["oauth_fail", "disk_critical"],
        )
    mock_stop.assert_called_once_with("proj")


def test_given_plugin_critical_not_in_allowed_list_when_on_alert_then_stop_not_called(
    tmp_path: Path,
) -> None:
    alert = _make_alert("my_plugin_critical")
    with patch("agent_runner.monitor._call_local_stop") as mock_stop:
        on_alert(
            alert,
            project="proj",
            log_dir=tmp_path,
            allowed_stop_names=["oauth_fail", "disk_critical"],
        )
    mock_stop.assert_not_called()


def test_given_plugin_critical_explicitly_opted_in_when_on_alert_then_stop_called(
    tmp_path: Path,
) -> None:
    alert = _make_alert("my_plugin_critical")
    with patch("agent_runner.monitor._call_local_stop") as mock_stop:
        on_alert(
            alert,
            project="proj",
            log_dir=tmp_path,
            allowed_stop_names=["oauth_fail", "disk_critical", "my_plugin_critical"],
        )
    mock_stop.assert_called_once_with("proj")


def test_given_non_stop_action_when_on_alert_then_stop_not_called(
    tmp_path: Path,
) -> None:
    alert = _make_alert("plain_warning", auto_action="none")
    with patch("agent_runner.monitor._call_local_stop") as mock_stop:
        on_alert(
            alert,
            project="proj",
            log_dir=tmp_path,
            allowed_stop_names=["oauth_fail"],
        )
    mock_stop.assert_not_called()


def test_given_no_allowed_list_when_on_alert_then_backward_compat_allows_builtins(
    tmp_path: Path,
) -> None:
    """Backward compatibility: ``allowed_stop_names=None`` falls back to the
    legacy builtin pair (oauth_fail + disk_critical)."""
    alert = _make_alert("oauth_fail")
    with patch("agent_runner.monitor._call_local_stop") as mock_stop:
        on_alert(alert, project="proj", log_dir=tmp_path)
    mock_stop.assert_called_once_with("proj")


class _StopLoopError(Exception):
    """Sentinel to break the monitor generator's infinite loop after one alert."""


def test_monitor_loop_passes_work_dir_path_not_bare_name_to_on_alert(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop must hand on_alert the work_dir Path, not the bare project name:
    api.stop resolves a name's log_dir cwd-dependently, so a monitor run from a
    cwd != work_dir with a non-preset log_dir would target the wrong dir."""
    monkeypatch.setenv("HOME", str(tmp_git_repo))
    api.init(tmp_git_repo, force=False, commit=False)
    monkeypatch.setattr("agent_runner.api._poll_once", lambda *a, **k: [_make_alert("oauth_fail")])
    captured: dict[str, object] = {}

    def fake_on_alert(_alert, *, project, log_dir, allowed_stop_names):
        captured["project"] = project
        raise _StopLoopError

    monkeypatch.setattr("agent_runner.monitor.on_alert", fake_on_alert)
    with pytest.raises(_StopLoopError):
        for _ in api._monitor_loop_iter(tmp_git_repo):
            pass
    assert isinstance(captured["project"], Path)
    assert captured["project"] == tmp_git_repo


def test_auto_stop_resolves_real_log_dir_when_cwd_differs(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """on_alert given the work_dir Path resolves the project's ACTUAL (non-preset)
    log_dir — the serve.pid there is found and signaled — even from a foreign cwd,
    where a bare project name would resolve to an empty ~/.agent-runner/<name>/logs
    and no-op while serve keeps running."""
    monkeypatch.setenv("HOME", str(tmp_git_repo / "home"))
    api.init(tmp_git_repo, force=False, commit=False)
    toml = tmp_git_repo / "agent-runner.toml"
    custom_log_dir = tmp_git_repo / "mylogs"
    toml.write_text(
        re.sub(r"(?m)^log_dir = .*$", f'log_dir = "{custom_log_dir}"', toml.read_text(), count=1)
    )
    custom_log_dir.mkdir(parents=True)
    (custom_log_dir / "serve.pid").write_text("12345")
    # Keep detect_service_mode away from any real user-systemd unit, and run from a
    # cwd that is NOT the work_dir so a bare-name resolution would miss the pidfile.
    monkeypatch.setattr("agent_runner.lifecycle._user_systemd_dir", lambda: tmp_git_repo / "nou")
    foreign = tmp_git_repo / "elsewhere"
    foreign.mkdir()
    monkeypatch.chdir(foreign)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "agent_runner.api.send_signal_to_pid", lambda pid, sig: sent.append((pid, sig)) or True
    )
    monkeypatch.setattr("agent_runner.api.pid_alive", lambda pid: False)

    on_alert(
        _make_alert("oauth_fail"),
        project=tmp_git_repo,
        log_dir=custom_log_dir,
        allowed_stop_names=["oauth_fail"],
    )

    assert (12345, signal.SIGTERM) in sent  # the real log_dir's pidfile was signaled
