"""Alert-kind catalog + the plugin detector registry.

Pure bookkeeping: no filesystem I/O, no clock reads. Detector *logic* lives in
``_monitor_detectors``; state assembly in ``_monitor_state``; the cycle-edge
wiring (``run_all_detectors``/``on_alert``) in ``monitor.py``.
"""

from __future__ import annotations

from agent_runner._registry import ensure_unique
from agent_runner.api_types import Alert, Detector
from agent_runner.config import _DEFAULT_AUTO_STOP_ON
from agent_runner.events import (
    DETECTOR_ERROR,
    MONITOR_ALERT_EMITTED,
    MONITOR_AUTO_STOP_FAILED,
    MONITOR_AUTO_STOP_TRIGGERED,
    MONITOR_REMOTE_BLIP,
    MONITOR_REMOTE_GIVEUP,
    MONITOR_STARTED,
)

KNOWN_ALERT_KINDS: frozenset[str] = frozenset(
    {
        "timeout_rate",
        "hung",
        "orphan_chain",
        "disk_warning",
        "disk_critical",
        "mem_pressure",
        "oauth_fail",
        "network_fail",
        "rate_limit_active",
        "anomaly_repetitive_active",
        "supervisor_stale",
    }
)

_MONITOR_SELF_KINDS: frozenset[str] = frozenset(
    {
        MONITOR_ALERT_EMITTED,
        MONITOR_AUTO_STOP_FAILED,
        MONITOR_AUTO_STOP_TRIGGERED,
        MONITOR_REMOTE_BLIP,
        MONITOR_REMOTE_GIVEUP,
        MONITOR_STARTED,
        DETECTOR_ERROR,
    }
)
"""Kinds a monitor process writes into the very stream it reads. Excluded from
detect_supervisor_stale's freshness baseline (ouroboros: the monitor must not
measure its own emissions, or a busy monitor over a dead supervisor never alarms)."""

# Built-in detectors whose ``auto_action="stop_service"`` is honored by default
# (continuing in either state actively harms the host: burning API quota / writing
# to a near-full disk). Runtime gating reads ``cfg.monitor.auto_stop_on``; this is
# ``on_alert``'s fallback when no allow-list is supplied, and is what ``_docgen``
# renders as the default policy in docs/architecture.md. Derived from config's
# SSOT so the doc cannot publish a policy the loader does not apply --
# tests/invariants/test_auto_stop_policy_ssot.py pins it.
AUTO_STOP_ALERTS: frozenset[str] = frozenset(_DEFAULT_AUTO_STOP_ON)

_ALERT_IDENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    # Volatile fields (elapsed_s / streak / age_s / matches / value) are excluded;
    # only fields that name the *episode* remain, so one episode dedups to one alert.
    "hung": ("round_num",),
    "orphan_chain": ("last_round",),
    "rate_limit_active": ("agent", "throttled_until_iso"),
    "anomaly_repetitive_active": ("latest_tool", "latest_target"),
    "supervisor_stale": ("last_ts",),
}


def alert_identity(alert: Alert) -> str:
    """Stable dedup key for one alert episode. Rate-crossing detectors (timeout_rate,
    disk_*, mem_pressure, oauth_fail, network_fail) key on the detector name alone;
    others key on the episode-identifying context fields enumerated above."""
    fields = _ALERT_IDENTITY_FIELDS.get(alert.detector)
    if not fields:
        return alert.detector
    parts = "|".join(f"{k}={alert.context.get(k)!r}" for k in fields)
    return f"{alert.detector}:{parts}"


_PLUGIN_DETECTORS: list[Detector] = []


def register_detector(detector: Detector) -> None:
    """Register a plugin detector. Rejects duplicate names."""
    ensure_unique(detector.name, _PLUGIN_DETECTORS, "detector")
    _PLUGIN_DETECTORS.append(detector)


def plugin_detectors() -> list[str]:
    """Sorted list of registered plugin detector names (for peek --json)."""
    return sorted(d.name for d in _PLUGIN_DETECTORS)
