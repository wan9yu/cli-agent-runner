"""Monitor — the cycle-edge: running detectors and dispatching on their alerts.

13 built-in detectors run every poll (see ``_monitor_detectors``); two trigger
``auto_action="stop_service"``:
  * oauth_fail  — agent-reported auth failures, or an auth pattern in
    short-exit logs (retrying burns API quota)
  * disk_critical — disk_used_pct > 95% (writing more risks corruption)

The detector logic, the plugin registry, and state assembly are pure and live
in ``_monitor_detectors``/``_monitor_registry``/``_monitor_state`` — split out
for module-size hygiene. This module keeps only the cycle edge: running every
detector in isolation (``run_all_detectors``), running plugin detectors
(``run_plugin_detectors``), and auto-stop dispatch (``on_alert``). Every name
those three pure modules define is re-exported below so ``agent_runner.monitor``
stays the one import surface for callers and plugin authors.

Detection is always on-host: every source reads the local filesystem and
auto-stop stops the local service. Remote observation is a separate concern —
an event relay, ``agent_runner/remote_relay.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

# Detectors are imported by BARE NAME (not `_monitor_detectors.detect_x`) so
# `run_all_detectors` calls them as plain globals — the pattern
# `monkeypatch.setattr("agent_runner.monitor.detect_timeout_rate", ...)`
# (tests/unit/test_detector_isolation.py) replaces the name in THIS module's
# namespace, which is exactly what a bare-name call looks up at call time.
# Patching `_monitor_detectors.detect_timeout_rate` instead would silently not
# land here — see that test's docstring.
from agent_runner._monitor_detectors import (
    detect_anomaly_repetitive_active,
    detect_disk_critical,
    detect_disk_warning,
    detect_hung,
    detect_mem_pressure,
    detect_mem_pressure_gate_inert,
    detect_network_fail,
    detect_oauth_fail,
    detect_orphan_chain,
    detect_rate_limit_active,
    detect_supervisor_stale,
    detect_timeout_rate,
)
from agent_runner._monitor_registry import _PLUGIN_DETECTORS, AUTO_STOP_ALERTS
from agent_runner.api_types import Alert, ProjectState, ServiceMode, ServiceStatus
from agent_runner.clock import SYSTEM_CLOCK
from agent_runner.config import MonitorHostHealthConfig, PhaseOverride
from agent_runner.events import (
    DETECTOR_ERROR,
    MONITOR_ALERT_EMITTED,
    MONITOR_AUTO_STOP_FAILED,
    MONITOR_AUTO_STOP_TRIGGERED,
)
from agent_runner.events import (
    emit as emit_event,
)

# What on_alert actually did with this alert, so its caller (_monitor_loop_iter)
# can decide whether the dedup `seen` episode should be forced to re-arm.
# "draining" is the one verdict that must re-fire on the NEXT poll (nothing was
# recorded — see on_alert's docstring); the other three are terminal for this
# episode (recorded triggered/failed, or never eligible for a stop at all) and
# keep the normal dedup (stay suppressed until the alert clears a poll).
OnAlertVerdict = Literal["triggered", "failed", "draining", "none"]


def _run_detector(
    name: str, fn: Callable[[], Alert | None], *, log_dir: Path | None
) -> Alert | None:
    """Run one builtin detector, isolating a crash the way run_plugin_detectors
    isolates plugins: emit ``detector_error`` (when a log_dir is available) and
    return None so the remaining detectors still run."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — one bad detector must not blind the rest
        if log_dir is not None and log_dir.is_dir():
            emit_event(log_dir, DETECTOR_ERROR, detector=name, error=f"{type(e).__name__}: {e}")
        return None


def run_all_detectors(
    *,
    events: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    log_tails: dict[int, str],
    round_timeout_s: int = 1800,
    supervisor_stale_threshold_s: int | None = None,
    now: datetime | None = None,
    auth_fail_patterns: list[str] | None = None,
    auth_fail_hint: str | None = None,
    phases_overrides: dict[str, PhaseOverride] | None = None,
    mem_avail_min_mb: int = 200,
    disk_warning_pct: float = 90.0,
    disk_critical_pct: float = 95.0,
    swap_sout_noise_floor_mb: int = 32,
    mem_free_low_mb: int = 16,
    log_dir: Path | None = None,
) -> list[Alert]:
    """Run all 13 detectors; returns alerts (empty = healthy).

    Each detector is isolated via ``_run_detector``: a crash in one emits
    ``detector_error`` (when ``log_dir`` is given) and does not stop the rest.
    """
    if now is None:
        now = SYSTEM_CLOCK.now_utc()
    compiled_auth_pats = (
        [re.compile(p, re.IGNORECASE) for p in auth_fail_patterns] if auth_fail_patterns else None
    )
    effective_stale_s = (
        int(round_timeout_s * 1.5)
        if supervisor_stale_threshold_s is None
        else supervisor_stale_threshold_s
    )
    host_health_cfg = MonitorHostHealthConfig(
        mem_avail_min_mb=mem_avail_min_mb,
        disk_warning_pct=disk_warning_pct,
        disk_critical_pct=disk_critical_pct,
        swap_sout_noise_floor_mb=swap_sout_noise_floor_mb,
        mem_free_low_mb=mem_free_low_mb,
    )
    detectors: list[tuple[str, Callable[[], Alert | None]]] = [
        ("timeout_rate", lambda: detect_timeout_rate(events)),
        (
            "hung",
            lambda: detect_hung(
                events, now=now, round_timeout_s=round_timeout_s, phases_overrides=phases_overrides
            ),
        ),
        ("orphan_chain", lambda: detect_orphan_chain(events)),
        (
            "disk_warning",
            lambda: detect_disk_warning(
                metrics, threshold_pct=disk_warning_pct, critical_pct=disk_critical_pct
            ),
        ),
        ("disk_critical", lambda: detect_disk_critical(metrics, threshold_pct=disk_critical_pct)),
        ("mem_pressure", lambda: detect_mem_pressure(metrics, cfg=host_health_cfg)),
        (
            "mem_pressure_gate_inert",
            lambda: detect_mem_pressure_gate_inert(metrics, cfg=host_health_cfg),
        ),
        (
            "oauth_fail",
            lambda: detect_oauth_fail(
                events, log_tails, patterns=compiled_auth_pats, hint=auth_fail_hint
            ),
        ),
        ("network_fail", lambda: detect_network_fail(events, log_tails)),
        (
            "rate_limit_active",
            lambda: detect_rate_limit_active(events, now=now.timestamp(), log_dir=log_dir),
        ),
        ("anomaly_repetitive_active", lambda: detect_anomaly_repetitive_active(events)),
        (
            "supervisor_stale",
            lambda: detect_supervisor_stale(events, now=now, stale_threshold_s=effective_stale_s),
        ),
    ]
    candidates = [_run_detector(name, fn, log_dir=log_dir) for name, fn in detectors]
    return [a for a in candidates if a is not None]


def run_plugin_detectors(state: ProjectState) -> list[Alert]:
    """Invoke every registered plugin detector with the current ProjectState.

    Plugin failures are isolated: an exception inside one detector is logged
    via ``UserWarning`` and the remaining detectors continue. No alert is
    emitted on plugin crash — only the warning surfaces.

    Builtin detectors run separately via ``run_all_detectors``; the two lists
    of alerts are typically concatenated by the caller (``api._poll_once``).
    """
    import warnings

    out: list[Alert] = []
    for detector in _PLUGIN_DETECTORS:
        try:
            alert = detector.detect(state)
        except Exception as e:
            warnings.warn(
                f"plugin detector {detector.name!r} raised during detect(): {e}",
                stacklevel=2,
            )
            continue
        if alert is not None:
            out.append(alert)
    return out


# ---------------------------------------------------------------------------
# Auto-stop dispatch
# ---------------------------------------------------------------------------


class MonitorRemoteUnsupportedError(Exception):
    """Raised at monitor startup (``api.monitor_loop``) when ``--host`` is passed
    to a detection mode.

    Detection is on-host by design: the detectors read round logs and metrics
    from the local filesystem, and ``auto_stop_on`` acts on the local service
    with no client in the loop. Only ``--mode events`` is relayable.
    """

    def __init__(self, host: str, mode: str = "anomaly") -> None:
        super().__init__(
            f"remote monitoring (--host {host}) is unsupported for --mode {mode}: "
            "detection runs on the supervised host by design — the detectors read "
            "that host's logs and stop that host's service with no client involved.\n"
            f"Run it there: ssh {host}, then "
            "agent-runner monitor --config ~/.agent-runner/<project>/agent-runner.toml "
            "(or install it as a unit: agent-runner install --monitor).\n"
            f"For a remote event stream from this machine: "
            f"agent-runner monitor --host {host} --mode events\n"
            'See docs/runbook.md, section "Remote event relay & SSH trust".'
        )
        self.host = host


def _call_local_stop(project: str | Path) -> ServiceStatus:
    # Late import: api imports monitor for peek, so we defer the reverse direction.
    from agent_runner import api

    return api.stop(project)


def on_alert(
    alert: Alert,
    *,
    project: str | Path,
    log_dir: Path,
    allowed_stop_names: list[str] | None = None,
) -> OnAlertVerdict:
    """Record the alert and, if auto_action==stop_service AND the detector
    name is in ``allowed_stop_names``, stop the service. Returns the verdict
    (see :data:`OnAlertVerdict`) so ``_monitor_loop_iter`` — its only caller —
    can force its dedup ``seen`` episode to re-arm on a ``"draining"`` verdict,
    since nothing was recorded for that alert THIS poll and it must be given a
    genuine next attempt (see below).

    ``allowed_stop_names`` defaults to the legacy builtin pair when not
    supplied; callers with access to ``cfg.monitor.auto_stop_on`` should
    pass it through so operators can opt plugin detectors in/out.

    The stop is always local: the monitor runs on the host it supervises.

    ``MONITOR_AUTO_STOP_TRIGGERED`` is emitted only AFTER ``api.stop`` returns
    ``active=False`` — a genuinely confirmed stop, not just an attempt. A mode
    mismatch (stale unit reference, wrong pidfile) makes ``api.stop`` a silent
    no-op that returns normally with ``active`` still True; without this
    ordering that no-op would be misreported as a successful stop and the
    ``except`` below (for a raise) would never even see it.

    ``api.stop``'s PID_FILE confirm window (``_PID_SIGNAL_GRACE_S``) is
    intentionally short so the monitor loop never blocks for anywhere close to
    a round's full timeout — a round in flight when SIGTERM lands is, by
    design, NOT interrupted (``cli/_serve_round.py``'s ``_spawn_round``
    docstring: serve drains its current round before exiting), and even
    outside a round serve may still be bootstrapping the next one or sleeping
    in its restart back-off. So ``active=True`` after that window means one of
    two very different things depending on mode. In PID_FILE mode, ``result``
    came from ``api.stop`` → ``status()``, whose PID_FILE branch computes
    ``active`` from that same ``serve.pid`` as ``pid is not None and
    pid_alive(pid)`` — so ``active=True`` here already means the serve pid
    itself is alive, i.e. still draining (mid-round, still bootstrapping the
    next round, or asleep in its restart back-off) and has not yet had its
    next chance to notice the SIGTERM and exit. That case returns
    ``"draining"`` and records NOTHING here (recording a failure would be a
    false alarm for a stop that is working exactly as designed) — the caller
    re-arms `seen` for it so the SAME alert is handed to ``on_alert`` again on
    the very next poll, by which point the drain has usually resolved one way
    or the other, and THAT call records the truthful ``triggered``/``failed``
    outcome. A genuine failure is everything else still active after the
    window: any non-PID_FILE mode still reporting active (e.g. a stale unit
    reference) — nothing below will ever change this, it never gets a
    "draining" verdict, so the dedup ``seen`` entry is left standing and this
    exact alert stays suppressed until it clears a poll and recurs — or a
    raised ``api.stop`` call (handled above). (The one accepted edge: a serve
    that NEVER honors SIGTERM re-fires "draining" forever rather than ever
    recording failed.)
    """
    effective_allowed = (
        allowed_stop_names if allowed_stop_names is not None else list(AUTO_STOP_ALERTS)
    )

    def _emit_if_dir(kind: str, **fields: Any) -> None:
        if log_dir.is_dir():
            emit_event(log_dir, kind, detector=alert.detector, **fields)

    _emit_if_dir(
        MONITOR_ALERT_EMITTED,
        severity=alert.severity,
        message=alert.message,
        auto_action=alert.auto_action,
    )
    if alert.auto_action != "stop_service":
        return "none"
    if alert.detector not in effective_allowed:
        return "none"  # gated — operator has not opted this detector into auto-stop
    try:
        result = _call_local_stop(project)
    except Exception as e:
        # Any stop failure (unit missing, permission denied, stale pidfile) is
        # recorded and swallowed: crashing the monitor here would take out the
        # supervision that noticed the problem. The dedup `seen` entry is left
        # standing (a "failed" verdict does not re-arm it), so on_alert
        # re-fires only after this alert clears from a poll and recurs — not
        # on the very next poll.
        _emit_if_dir(MONITOR_AUTO_STOP_FAILED, error=f"{type(e).__name__}: {e}")
        return "failed"
    if result.active:
        # api.stop returned without raising but the service is STILL active —
        # either the silent no-op this fix closes (e.g. a mode mismatch that
        # signaled nothing, or a dead/stale pidfile) or a still-live PID_FILE
        # serve genuinely draining (mid-round, starting, or waking — see
        # on_alert's docstring). In PID_FILE mode, active=True already IS
        # serve-pid liveness (status()'s PID_FILE branch computes it from the
        # same serve.pid), so it can only be the latter — no re-derivation
        # needed. Only a still-active non-PID_FILE mode is a real failure.
        if result.mode == ServiceMode.PID_FILE:
            return "draining"
        _emit_if_dir(
            MONITOR_AUTO_STOP_FAILED,
            error=f"stop did not take effect (mode={result.mode.value}, still active)",
        )
        return "failed"
    _emit_if_dir(MONITOR_AUTO_STOP_TRIGGERED)
    return "triggered"


# ---------------------------------------------------------------------------
# Facade — re-export the pure layers so `agent_runner.monitor` stays the one
# import surface (attribute access AND `from agent_runner.monitor import X`)
# for runner.py (NETWORK_PATTERNS), _docgen.py (KNOWN_ALERT_KINDS), cli.common
# (plugin_detectors), __init__.py (_PLUGIN_DETECTORS — same list object, so
# in-place mutation through either name stays visible to both), api.py
# (LocalSource/StateSource/assemble_project_state/load_round_log_tails/
# alert_identity/_EventTail), and every existing test patch target.
# ---------------------------------------------------------------------------
from agent_runner._monitor_detectors import (  # noqa: E402,F401 — intentional bottom re-export
    NETWORK_PATTERNS,
    SHORT_EXIT_THRESHOLD_S,
    _alert,
    _last_n_round_exits,
    _latest,
    _latest_schedule_event,
    _phase_timeout,
    latest_schedule_state,
)
from agent_runner._monitor_registry import (  # noqa: E402,F401 — intentional bottom re-export
    _ALERT_IDENTITY_FIELDS,
    _MONITOR_SELF_KINDS,
    KNOWN_ALERT_KINDS,
    alert_identity,
    plugin_detectors,
    register_detector,
)
from agent_runner._monitor_state import (  # noqa: E402,F401 — intentional bottom re-export
    LocalSource,
    StateSource,
    _EventTail,
    _latest_metric_dict,
    assemble_project_state,
    load_round_log_tails,
    parse_events_from_jsonl_files,
)
