"""Serve-boot sandbox gate: tri-state ``[plugins] sandbox`` behavior at boot.

Every branch here is platform-agnostic -- ``probe_sandbox_capability`` is
monkeypatched, so this exercises the gate's decision logic on macOS too. The
gate reports ``achieved_tier="unconfined"`` for real on any non-Linux dev
host (see ``probe_sandbox_capability``'s own non-linux branch), which is
exactly the ``require``-aborts / ``prefer``-degrades shape asserted below;
only the "sandbox actually ENGAGES" case needs a real Linux probe, and that
is exercised on Linux CI (``tests/linux/test_plugin_sandbox_kill.py``), not
here.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runner import _sandbox_probe
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import make_cfg
from tests._test_helpers import read_events_for_current_month as read_events


@dataclass
class _Probe:
    achieved_tier: str
    landlock_abi: int | None = None
    seccomp: bool = False
    unconfined_reason: str | None = "test"


def test_gate_should_abort_when_require_cannot_engage(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(_sandbox_probe, "probe_sandbox_capability", lambda: _Probe("unconfined"))
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(sandbox="require"))

    proceed = _sandbox_probe.gate_serve_boot(cfg, tmp_path)

    assert proceed is False


def test_gate_should_proceed_and_degrade_once_when_prefer_cannot_engage(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(_sandbox_probe, "probe_sandbox_capability", lambda: _Probe("unconfined"))
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(sandbox="prefer"))

    proceed = _sandbox_probe.gate_serve_boot(cfg, tmp_path)

    degrades = [e for e in read_events(tmp_path) if e["event"] == "plugin_sandbox_degraded"]
    assert proceed is True
    assert len(degrades) == 1


def test_gate_should_proceed_silently_when_fully_engaged(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        _sandbox_probe, "probe_sandbox_capability", lambda: _Probe("landlock+seccomp")
    )
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(sandbox="prefer"))

    proceed = _sandbox_probe.gate_serve_boot(cfg, tmp_path)

    degrades = [e for e in read_events(tmp_path) if e["event"] == "plugin_sandbox_degraded"]
    assert proceed is True
    assert degrades == []


def test_gate_should_proceed_silently_when_sandbox_off(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(_sandbox_probe, "probe_sandbox_capability", lambda: _Probe("unconfined"))
    cfg = make_cfg(tmp_path, plugins=PluginsConfig(sandbox="off"))

    proceed = _sandbox_probe.gate_serve_boot(cfg, tmp_path)

    degrades = [e for e in read_events(tmp_path) if e["event"] == "plugin_sandbox_degraded"]
    assert proceed is True
    assert degrades == []
