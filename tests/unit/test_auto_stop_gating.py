"""Tests for ``on_alert``'s strict auto-stop gating via ``allowed_stop_names``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
            host=None,
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
            host=None,
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
            host=None,
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
            host=None,
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
        on_alert(alert, project="proj", host=None, log_dir=tmp_path)
    mock_stop.assert_called_once_with("proj")
