"""End-to-end: a registered plugin detector's alert reaches monitor output."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import monitor
from agent_runner.api_types import Alert


@pytest.fixture(autouse=True)
def _reset_plugin_detectors():
    saved = list(monitor._PLUGIN_DETECTORS)
    monitor._PLUGIN_DETECTORS.clear()
    yield
    monitor._PLUGIN_DETECTORS.clear()
    monitor._PLUGIN_DETECTORS.extend(saved)


class _AlwaysFiresDetector:
    name = "always_fires"
    severity = "warning"
    auto_action = "none"

    def detect(self, state):
        return Alert(
            severity=self.severity,
            detector=self.name,
            message="test alert",
            context={"reason": "fixture"},
            ts="2026-01-01T00:00:00.000Z",
            auto_action=self.auto_action,
        )


def test_given_registered_plugin_detector_alert_returned() -> None:
    """Smoke-test the plugin path independent of full api._poll_once setup."""
    from agent_runner.api_types import (
        ProjectState,
        ServiceMode,
        ServiceStatus,
        SystemMetrics,
    )
    from agent_runner.monitor import run_plugin_detectors

    monitor.register_detector(_AlwaysFiresDetector())
    state = ProjectState(
        project="t",
        status={},
        defenses=[],
        current_round=None,
        recent_rounds=[],
        orphan=None,
        system=SystemMetrics(mem_total_mb=1, mem_available_mb=1, disk_used_pct=0.0),
        service=ServiceStatus(mode=ServiceMode.NONE, active=False),
    )
    alerts = run_plugin_detectors(state)
    assert len(alerts) == 1
    assert alerts[0].detector == "always_fires"


def test_given_crashing_plugin_detector_when_invoked_then_warns_and_continues(
    tmp_path: Path,
) -> None:
    """A plugin detector's exception during detect() does not propagate."""
    import warnings

    class _Crashes:
        name = "boom"
        severity = "critical"
        auto_action = "none"

        def detect(self, state):
            raise RuntimeError("simulated detector crash")

    from agent_runner.api_types import (
        ProjectState,
        ServiceMode,
        ServiceStatus,
        SystemMetrics,
    )
    from agent_runner.monitor import run_plugin_detectors

    monitor.register_detector(_Crashes())
    monitor.register_detector(_AlwaysFiresDetector())
    state = ProjectState(
        project="t",
        status={},
        defenses=[],
        current_round=None,
        recent_rounds=[],
        orphan=None,
        system=SystemMetrics(mem_total_mb=1, mem_available_mb=1, disk_used_pct=0.0),
        service=ServiceStatus(mode=ServiceMode.NONE, active=False),
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        alerts = run_plugin_detectors(state)
    assert any(a.detector == "always_fires" for a in alerts)
    assert not any(a.detector == "boom" for a in alerts)
    assert any("boom" in str(w.message) for w in caught), (
        f"expected warning mentioning 'boom'; got {[str(w.message) for w in caught]}"
    )
