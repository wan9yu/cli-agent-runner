"""Invariant: every monitor-authored event kind is excluded from the staleness baseline.

``detect_supervisor_stale`` treats the event stream as evidence the supervisor is
alive. But the monitor process itself writes into that same stream
(monitor_alert_emitted, monitor_started, detector_error, ...). Any monitor-authored
kind missing from ``monitor._MONITOR_SELF_KINDS`` would let the monitor's own
emissions reset the freshness baseline, masking a dead supervisor (ouroboros one
layer up). This scans the live ``monitor_*`` kind registry so a newly added
monitor-authored kind cannot silently fall outside the exclusion set.
"""

from __future__ import annotations

from agent_runner import events, monitor


def test_baseline_excludes_every_monitor_authored_kind() -> None:
    monitor_kinds = {k for k in events._BUILTIN_KINDS if k.startswith("monitor_")}
    # vacuity-guard
    assert monitor_kinds, "no monitor_* kinds found in _BUILTIN_KINDS -- scan is vacuous"
    assert monitor_kinds <= monitor._MONITOR_SELF_KINDS
    assert events.DETECTOR_ERROR in monitor._MONITOR_SELF_KINDS  # monitor-authored, not monitor_*
