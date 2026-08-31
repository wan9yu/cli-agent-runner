from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from agent_runner.api_types import Alert
from agent_runner.cli import monitor_cmd


def test_plugin_alert_with_unknown_severity_does_not_crash(capsys) -> None:
    odd = Alert(severity="notice", detector="plug", message="hi", context={}, ts="t")  # type: ignore[arg-type]
    args = SimpleNamespace(interval=None, json=False, config=None, work_dir=None)
    with (
        patch.object(monitor_cmd.api, "monitor_loop", return_value=iter([odd])),
        patch.object(monitor_cmd, "work_dir_from_args", return_value="."),
    ):
        rc = monitor_cmd._cmd_anomaly(args)
    assert rc == 0
    assert "plug" in capsys.readouterr().out  # printed, not KeyError'd
