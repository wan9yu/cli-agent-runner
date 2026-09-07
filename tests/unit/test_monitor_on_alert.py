"""Tests for ``on_alert``'s fail-open ordering: the stop must land even when
every breadcrumb write on the path fails (ENOSPC — the ``disk_critical``
failure domain itself)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import monitor
from agent_runner.api_types import Alert, ServiceMode, ServiceStatus


def _make_alert(detector: str = "disk_critical", auto_action: str = "stop_service") -> Alert:
    return Alert(
        severity="critical",
        detector=detector,
        message="disk full",
        context={},
        ts="2026-01-01T00:00:00.000Z",
        auto_action=auto_action,
    )


def test_on_alert_emit_failure_does_not_prevent_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ENOSPC breadcrumb write (the disk_critical failure domain itself) must
    NOT crash on_alert before it stops serve: stop lands first, emit is best-effort."""
    stopped = {"called": False}

    def fake_stop(_project: object) -> ServiceStatus:
        stopped["called"] = True
        return ServiceStatus(mode=ServiceMode.PID_FILE, active=False, pid=None)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(monitor, "_call_local_stop", fake_stop)
    monkeypatch.setattr(monitor, "emit_event", boom)  # every breadcrumb write fails
    alert = _make_alert("disk_critical")

    verdict = monitor.on_alert(
        alert, project=tmp_path, log_dir=tmp_path, allowed_stop_names=["disk_critical"]
    )

    assert stopped["called"] is True  # stop happened despite the poisoned emitter
    assert verdict == "triggered"  # confirmed stop still classified correctly


def test_on_alert_emit_failure_does_not_hide_stop_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A poisoned emitter must not swallow a genuine stop failure either — the
    verdict is still "failed", not silently "none" or a crash."""

    def failing_stop(_project: object) -> ServiceStatus:
        raise RuntimeError("unit missing")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(monitor, "_call_local_stop", failing_stop)
    monkeypatch.setattr(monitor, "emit_event", boom)
    alert = _make_alert("disk_critical")

    verdict = monitor.on_alert(
        alert, project=tmp_path, log_dir=tmp_path, allowed_stop_names=["disk_critical"]
    )

    assert verdict == "failed"
