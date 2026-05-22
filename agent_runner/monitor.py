"""Monitor — anomaly detectors over events + metrics + log tails.

11 built-in detectors. Two trigger ``auto_action="stop_service"``:
  * oauth_fail  — auth pattern in short-exit logs (retrying burns API quota)
  * disk_critical — disk_used_pct > 95% (writing more risks corruption)

The detectors are pure functions; the loop, ssh fetch, and auto-stop wiring
live further down. Plugin detectors register via :func:`register_detector`
and run alongside the builtins on every poll.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agent_runner._registry import ensure_unique
from agent_runner.api_types import (
    Alert,
    Detector,
    ProjectState,
    ServiceMode,
    ServiceStatus,
    SystemMetrics,
)
from agent_runner.config import _DEFAULT_AUTH_PATTERNS, PhaseOverride
from agent_runner.context_store import read_json
from agent_runner.events import (
    TRANSIENT_ERROR_DETECTED,
    TRANSIENT_ERROR_RECOVERED,
    now_iso_ms,
    parse_iso_ms,
)
from agent_runner.events import (
    emit as emit_event,
)

KNOWN_ALERT_KINDS: frozenset[str] = frozenset(
    {
        "timeout_rate",
        "hung",
        "orphan_chain",
        "disk_warning",
        "disk_critical",
        "mem_pressure",
        "smoke_fail_rate",
        "oauth_fail",
        "network_fail",
        "rate_limit_active",
        "anomaly_repetitive_active",
    }
)

# Built-in detectors whose ``auto_action="stop_service"`` is honored by default
# (continuing in either state actively harms the host: burning API quota / writing
# to a near-full disk). Runtime gating reads ``cfg.monitor.auto_stop_on`` — this
# frozenset is the legacy fallback used by ``on_alert`` when no allow-list is
# supplied, and is surfaced by ``_docgen`` to document the default policy.
AUTO_STOP_ALERTS: frozenset[str] = frozenset({"oauth_fail", "disk_critical"})

_PLUGIN_DETECTORS: list[Detector] = []


def register_detector(detector: Detector) -> None:
    """Register a plugin detector. Rejects duplicate names."""
    ensure_unique(detector.name, _PLUGIN_DETECTORS, "detector")
    _PLUGIN_DETECTORS.append(detector)


def plugin_detectors() -> list[str]:
    """Sorted list of registered plugin detector names (for peek --json)."""
    return sorted(d.name for d in _PLUGIN_DETECTORS)


SHORT_EXIT_THRESHOLD_S = 60

NETWORK_PATTERNS = re.compile(
    r"\b(connection refused|econnrefused|dns|"
    r"name or service not known|connect(ion)? timed out|"
    r"nodename nor servname|network unreachable|"
    r"50[023] (service unavailable|bad gateway|gateway timeout)|"
    r"connection reset)\b",
    re.IGNORECASE,
)


def _alert(
    detector: str, severity: str, message: str, context: dict[str, Any], auto_action: str = "none"
) -> Alert:
    # Builtin-only helper. Plugin detectors construct Alert directly; their
    # names are not in KNOWN_ALERT_KINDS (validated by docgen + test_catalogs).
    return Alert(
        severity=severity,
        detector=detector,
        message=message,
        context=context,
        ts=now_iso_ms(),
        auto_action=auto_action,
    )


def _last_n_round_exits(events: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    exits = [e for e in events if e.get("event") == "agent_exit"]
    return exits[-n:]


def detect_timeout_rate(
    events: list[dict[str, Any]], *, window: int = 10, threshold: float = 0.2
) -> Alert | None:
    recent = _last_n_round_exits(events, window)
    if len(recent) < window:
        return None
    timed = sum(1 for e in recent if e.get("timed_out"))
    rate = timed / len(recent)
    if rate < threshold:
        return None
    return _alert(
        "timeout_rate",
        "warning",
        f"{timed}/{len(recent)} recent rounds timed out (>{threshold:.0%})",
        {"rate": rate, "threshold": threshold, "window": window},
    )


def _phase_timeout(
    phases_overrides: dict[str, PhaseOverride] | None, phase: str | None, fallback: int
) -> int:
    """Return effective timeout for the given phase, falling back to ``fallback``."""
    if phase is None or phases_overrides is None:
        return fallback
    override = phases_overrides.get(phase)
    if override is None or override.round_timeout_s is None:
        return fallback
    return override.round_timeout_s


def detect_hung(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    factor: float = 1.5,
    round_timeout_s: int = 1800,
    phases_overrides: dict[str, PhaseOverride] | None = None,
) -> Alert | None:
    """A round_start without a matching round_end after timeout * factor.

    When ``phases_overrides`` is supplied and the round's ``phase`` is in it
    with a ``round_timeout_s`` override, that value applies. Otherwise falls
    back to ``round_timeout_s``. Rounds with no recorded phase always use the
    global timeout.
    """
    open_rounds: dict[int, tuple[str, str | None]] = {}
    for e in events:
        kind = e.get("event")
        rn = e.get("round_num")
        if kind == "round_start" and rn is not None:
            open_rounds[rn] = (e["ts"], e.get("phase"))
        elif kind == "round_end" and rn in open_rounds:
            del open_rounds[rn]
    for rn, (started_ts, phase) in open_rounds.items():
        started = parse_iso_ms(started_ts)
        elapsed = (now - started).total_seconds()
        effective_timeout = _phase_timeout(phases_overrides, phase, round_timeout_s)
        threshold = effective_timeout * factor
        if elapsed > threshold:
            return _alert(
                "hung",
                "warning",
                f"Round {rn} started {elapsed:.0f}s ago with no round_end",
                {"round_num": rn, "elapsed_s": elapsed, "threshold_s": threshold},
            )
    return None


def detect_orphan_chain(events: list[dict[str, Any]], *, threshold: int = 3) -> Alert | None:
    rounds_in_order = [e for e in events if e.get("event") in ("round_end", "orphan_stashed")]
    streak = 0
    last_round_with_orphan: int | None = None
    for e in rounds_in_order:
        if e.get("event") == "orphan_stashed":
            streak += 1
            last_round_with_orphan = e.get("round_num")
        elif e.get("event") == "round_end":
            rn = e.get("round_num")
            has_orphan_for_round = any(
                o.get("event") == "orphan_stashed" and o.get("round_num") == rn
                for o in rounds_in_order
            )
            if not has_orphan_for_round:
                streak = 0
    if streak >= threshold:
        return _alert(
            "orphan_chain",
            "warning",
            f"{streak} consecutive rounds with orphan_stashed (>= {threshold})",
            {"streak": streak, "threshold": threshold, "last_round": last_round_with_orphan},
        )
    return None


def _latest(metrics: list[dict[str, Any]], key: str) -> Any:
    for m in reversed(metrics):
        if key in m:
            return m[key]
    return None


def detect_disk_warning(
    metrics: list[dict[str, Any]],
    *,
    threshold_pct: float = 90.0,
    critical_pct: float = 95.0,
) -> Alert | None:
    val = _latest(metrics, "disk_used_pct")
    if val is None or val < threshold_pct or val >= critical_pct:
        # >=critical_pct handled by detect_disk_critical
        return None
    return _alert(
        "disk_warning",
        "warning",
        f"disk_used_pct {val} >= {threshold_pct}",
        {
            "value": val,
            "threshold": threshold_pct,
            "hint": "Free space soon — clean ~/.agent-runner/<project>/logs/",
        },
    )


def detect_disk_critical(
    metrics: list[dict[str, Any]], *, threshold_pct: float = 95.0
) -> Alert | None:
    val = _latest(metrics, "disk_used_pct")
    if val is None or val < threshold_pct:
        return None
    return _alert(
        "disk_critical",
        "critical",
        f"disk_used_pct {val} >= {threshold_pct} — auto-stopping service",
        {"value": val, "threshold": threshold_pct, "hint": "Stop and clean disk before resuming"},
        auto_action="stop_service",
    )


def detect_mem_pressure(metrics: list[dict[str, Any]], *, threshold_mb: int = 200) -> Alert | None:
    val = _latest(metrics, "mem_available_mb")
    if val is None or val >= threshold_mb:
        return None
    return _alert(
        "mem_pressure",
        "warning",
        f"mem_available_mb {val} < {threshold_mb}",
        {
            "value": val,
            "threshold": threshold_mb,
            "hint": "Investigate memory leak or move to a larger host",
        },
    )


def detect_smoke_fail_rate(
    events: list[dict[str, Any]], *, window: int = 10, threshold: float = 0.1
) -> Alert | None:
    ends = [e for e in events if e.get("event") == "round_end"]
    if len(ends) < window:
        return None
    recent_round_nums = [e.get("round_num") for e in ends[-window:]]
    fails = sum(
        1
        for e in events
        if e.get("event") == "smoke_check_failed" and e.get("round_num") in recent_round_nums
    )
    rate = fails / window
    if rate < threshold:
        return None
    return _alert(
        "smoke_fail_rate",
        "warning",
        f"{fails}/{window} recent rounds had smoke_check_failed",
        {"rate": rate, "threshold": threshold, "hint": "Inspect events.jsonl for failure reasons"},
    )


def detect_oauth_fail(
    events: list[dict[str, Any]],
    log_tails: dict[int, str],
    *,
    window: int = 10,
    threshold: float = 0.2,
    patterns: list[re.Pattern[str]] | None = None,
    hint: str | None = None,
) -> Alert | None:
    pats = patterns or [re.compile(p, re.IGNORECASE) for p in _DEFAULT_AUTH_PATTERNS]
    recent = _last_n_round_exits(events, window)
    matches = sum(
        1
        for e in recent
        if (e.get("duration_s") or 0.0) < SHORT_EXIT_THRESHOLD_S
        and e.get("exit_code", 0) != 0
        and not e.get("timed_out", False)
        and any(p.search(log_tails.get(e.get("round_num"), "")) for p in pats)
    )
    total = len(recent)
    if total < window or matches / total < threshold:
        return None
    return _alert(
        "oauth_fail",
        "critical",
        f"{matches}/{total} recent rounds short-exited with auth failure pattern",
        {
            "matches": matches,
            "window": total,
            "threshold": threshold,
            "hint": hint if hint is not None else "",
        },
        auto_action="stop_service",
    )


def detect_network_fail(
    events: list[dict[str, Any]],
    log_tails: dict[int, str],
    *,
    window: int = 10,
    threshold: float = 0.2,
) -> Alert | None:
    recent = _last_n_round_exits(events, window)
    matches = sum(
        1
        for e in recent
        if (e.get("duration_s") or 0.0) < SHORT_EXIT_THRESHOLD_S
        and e.get("exit_code", 0) != 0
        and not e.get("timed_out", False)
        and NETWORK_PATTERNS.search(log_tails.get(e.get("round_num"), ""))
    )
    total = len(recent)
    if total < window or matches / total < threshold:
        return None
    return _alert(
        "network_fail",
        "warning",
        f"{matches}/{total} recent rounds short-exited with network error pattern",
        {
            "matches": matches,
            "window": total,
            "threshold": threshold,
            "hint": "Check upstream Anthropic status or local DNS / VPN",
        },
    )


def detect_rate_limit_active(
    events: list[dict[str, Any]], *, now: float | None = None
) -> Alert | None:
    """Fire warning alert if currently throttled (latest transient_error_detected
    has reset_at_epoch in future, no matching recovered after)."""
    if now is None:
        now = time.time()
    for ev in reversed(events):
        kind = ev.get("event")
        if kind == TRANSIENT_ERROR_RECOVERED:
            return None
        if kind == TRANSIENT_ERROR_DETECTED:
            if int(ev.get("reset_at_epoch", 0)) > now:
                iso = datetime.fromtimestamp(ev["reset_at_epoch"], UTC).isoformat()
                classification = ev.get("classification", "unknown")
                return _alert(
                    "rate_limit_active",
                    "warning",
                    f"throttled until {iso} ({classification})",
                    {
                        "throttled_until_iso": iso,
                        "classification": classification,
                        "agent": ev.get("agent", "unknown"),
                    },
                )
            return None
    return None


def detect_anomaly_repetitive_active(
    events: list[dict[str, Any]],
    *,
    threshold: int = 1,
    window_rounds: int = 5,
) -> Alert | None:
    """Notify-severity alert when anomaly_repetitive_tool events appear in recent rounds.

    Activates 0.1.31's anomaly_repetitive_tool event in monitor's alert flow,
    mirroring the rate_limit_active pattern (event consumer → alert).

    Default: any anomaly event in last 5 rounds triggers a warning. Operators can
    widen window_rounds or raise threshold if the default is too sensitive.
    """
    round_nums = [e["round_num"] for e in events if "round_num" in e]
    if not round_nums:
        return None
    max_round = max(round_nums)
    window_start = max_round - window_rounds + 1
    anomalies = [
        e
        for e in events
        if e.get("event") == "anomaly_repetitive_tool" and e.get("round_num", 0) >= window_start
    ]
    if len(anomalies) < threshold:
        return None
    latest = anomalies[-1]
    count = len(anomalies)
    return _alert(
        "anomaly_repetitive_active",
        "warning",
        (
            f"{count} anomaly_repetitive_tool event(s) in last {window_rounds} rounds; "
            f"latest: {latest.get('tool_name')} on {latest.get('target')!r} "
            f"({latest.get('count')}x in window {latest.get('window')})"
        ),
        {
            "count": count,
            "window_rounds": window_rounds,
            "latest_tool": latest.get("tool_name"),
            "latest_target": latest.get("target"),
        },
    )


def detect_supervisor_stale(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    stale_threshold_s: int,
) -> Alert | None:
    """Alert when the most recent event is older than ``stale_threshold_s``.

    Catches supervisor "silent-death": stuck between rounds (after round_end,
    before the next round_start) emitting no events. The event stream cannot
    distinguish that from a normal idle gap — only a deadline check can.

    ``stale_threshold_s <= 0`` disables the check (caller resolves the
    sentinel). Empty event list → no alert: that is "never started", not
    silent-death, and there is no baseline to measure staleness against.
    """
    if stale_threshold_s <= 0 or not events:
        return None
    last_ts_str = max((e["ts"] for e in events if "ts" in e), default=None)
    if last_ts_str is None:
        return None
    age_s = (now - parse_iso_ms(last_ts_str)).total_seconds()
    if age_s <= stale_threshold_s:
        return None
    return _alert(
        "supervisor_stale",
        "warning",
        f"No events for {int(age_s)}s (threshold {stale_threshold_s}s) — "
        f"supervisor may be stuck or dead. Last event: {last_ts_str}.",
        {"age_s": int(age_s), "threshold_s": stale_threshold_s, "last_ts": last_ts_str},
    )


# ---------------------------------------------------------------------------
# State-tree assembly (Task 3.2)
# ---------------------------------------------------------------------------


class StateSource(Protocol):
    """Local or remote source — returns paths to read."""

    def events_files(self) -> list[Path]: ...
    def metrics_files(self) -> list[Path]: ...
    def rounds_dir(self) -> Path: ...
    def status_path(self) -> Path: ...
    def orphan_path(self) -> Path: ...


@dataclass(frozen=True)
class LocalSource:
    log_dir: Path

    def events_files(self) -> list[Path]:
        return sorted(self.log_dir.glob("events-*.jsonl"))

    def metrics_files(self) -> list[Path]:
        return sorted(self.log_dir.glob("metrics-*.jsonl"))

    def rounds_dir(self) -> Path:
        return self.log_dir / "rounds"

    def status_path(self) -> Path:
        return self.log_dir / "status.json"

    def orphan_path(self) -> Path:
        return self.log_dir / "orphan-state.json"


def parse_events_from_jsonl_files(files: Iterable[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_round_log_tails(rounds_dir: Path, *, tail_lines: int = 50) -> dict[int, str]:
    tails: dict[int, str] = {}
    if not rounds_dir.is_dir():
        return tails
    for f in rounds_dir.glob("R*-*.log"):
        try:
            num = int(f.name.split("-", 1)[0][1:])
        except (ValueError, IndexError):
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        tails[num] = "\n".join(lines[-tail_lines:])
    return tails


def _latest_metric_dict(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return metrics[-1] if metrics else {}


def assemble_project_state(source: StateSource, *, project: str) -> ProjectState:
    metrics = parse_events_from_jsonl_files(source.metrics_files())
    status = read_json(source.status_path()) or {}
    orphan = read_json(source.orphan_path())
    latest = _latest_metric_dict(metrics)
    system = SystemMetrics(
        mem_total_mb=int(latest.get("mem_total_mb", 0)),
        mem_available_mb=int(latest.get("mem_available_mb", 0)),
        disk_used_pct=float(latest.get("disk_used_pct", 0.0)),
        disk_free_gb=float(latest.get("disk_free_gb", 0.0)),
        load_1m=latest.get("load_1m"),
        cpu_pct=latest.get("cpu_pct"),
        agent_process_count=int(latest.get("agent_process_count", 0)),
    )
    return ProjectState(
        project=project,
        status=status,
        defenses=[],
        current_round=None,
        recent_rounds=[],
        orphan=orphan,
        system=system,
        service=ServiceStatus(mode=ServiceMode.NONE, active=False),
    )


def run_all_detectors(
    *,
    events: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    log_tails: dict[int, str],
    round_timeout_s: int = 1800,
    now: datetime | None = None,
    auth_fail_patterns: list[str] | None = None,
    auth_fail_hint: str | None = None,
    phases_overrides: dict[str, PhaseOverride] | None = None,
    mem_avail_min_mb: int = 200,
    disk_warning_pct: float = 90.0,
    disk_critical_pct: float = 95.0,
) -> list[Alert]:
    """Run all 11 detectors; returns alerts (empty = healthy)."""
    if now is None:
        now = datetime.now(UTC)
    compiled_auth_pats = (
        [re.compile(p, re.IGNORECASE) for p in auth_fail_patterns] if auth_fail_patterns else None
    )
    candidates = [
        detect_timeout_rate(events),
        detect_hung(
            events,
            now=now,
            round_timeout_s=round_timeout_s,
            phases_overrides=phases_overrides,
        ),
        detect_orphan_chain(events),
        detect_disk_warning(
            metrics, threshold_pct=disk_warning_pct, critical_pct=disk_critical_pct
        ),
        detect_disk_critical(metrics, threshold_pct=disk_critical_pct),
        detect_mem_pressure(metrics, threshold_mb=mem_avail_min_mb),
        detect_smoke_fail_rate(events),
        detect_oauth_fail(events, log_tails, patterns=compiled_auth_pats, hint=auth_fail_hint),
        detect_network_fail(events, log_tails),
        detect_rate_limit_active(events, now=now.timestamp()),
        detect_anomaly_repetitive_active(events),
    ]
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
# Remote source + auto-stop dispatch (Task 3.3)
# ---------------------------------------------------------------------------

import subprocess  # noqa: TID251, E402 — monitor needs ssh + local stop subprocess


class MonitorRemoteError(Exception):
    """ssh failed at the protocol level (host unreachable, key reject, etc.).

    Raised by ``run_remote_command`` when ssh exits with rc=255 (its reserved
    code for ssh-level failures, per the ssh(1) man page). Command-level
    non-zero exits (e.g. rc=2 from ``ls`` on no-match) are NOT raised; callers
    inspect the returncode and decide.
    """

    def __init__(self, host: str, stderr: str) -> None:
        super().__init__(f"ssh to {host!r} failed: {stderr}")
        self.host = host
        self.stderr = stderr


def run_remote_command(host: str, cmd: str, *, timeout: int = 30) -> tuple[int, str]:
    """Run a single shell command over ssh; returns (returncode, stdout).

    Raises ``MonitorRemoteError`` when ssh itself fails (rc=255 — host
    unreachable, key reject, etc.). Command-level non-zero rcs are returned
    as-is so callers (like ``RemoteSource._list``) can decide whether to
    tolerate them.
    """
    # ssh treats arguments starting with '-' as option flags (e.g. -oProxyCommand=...),
    # which can execute arbitrary local commands. Reject explicitly.
    if host.startswith("-"):
        raise ValueError(f"invalid ssh host (starts with '-'): {host!r}")
    r = subprocess.run(
        ["ssh", host, cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if r.returncode == 255:
        raise MonitorRemoteError(host, r.stderr.strip())
    return r.returncode, r.stdout


@dataclass(frozen=True)
class RemoteSource:
    """Mirrors LocalSource but fetches paths via ssh ls; reads via cat."""

    host: str
    project: str

    def _remote_log_dir(self) -> str:
        return f"~/.agent-runner/{self.project}/logs"

    def _list(self, glob: str) -> list[Path]:
        _rc, out = run_remote_command(
            self.host, f"ls -1 {self._remote_log_dir()}/{glob} 2>/dev/null"
        )
        return [Path(line.strip()) for line in out.splitlines() if line.strip()]

    def events_files(self) -> list[Path]:
        return self._list("events-*.jsonl")

    def metrics_files(self) -> list[Path]:
        return self._list("metrics-*.jsonl")

    def rounds_dir(self) -> Path:
        return Path(f"{self._remote_log_dir()}/rounds")

    def status_path(self) -> Path:
        return Path(f"{self._remote_log_dir()}/status.json")

    def orphan_path(self) -> Path:
        return Path(f"{self._remote_log_dir()}/orphan-state.json")


def _call_local_stop(project: str) -> None:
    # Late import: api imports monitor for peek, so we defer the reverse direction.
    from agent_runner import api

    api.stop(project)


def on_alert(
    alert: Alert,
    *,
    project: str,
    host: str | None,
    log_dir: Path,
    allowed_stop_names: list[str] | None = None,
) -> None:
    """Record the alert and, if auto_action==stop_service AND the detector
    name is in ``allowed_stop_names``, stop the service.

    ``allowed_stop_names`` defaults to the legacy builtin pair when not
    supplied; callers with access to ``cfg.monitor.auto_stop_on`` should
    pass it through so operators can opt plugin detectors in/out.
    """
    effective_allowed = (
        allowed_stop_names if allowed_stop_names is not None else list(AUTO_STOP_ALERTS)
    )
    if log_dir.is_dir():
        emit_event(
            log_dir,
            "monitor_alert_emitted",
            detector=alert.detector,
            severity=alert.severity,
            message=alert.message,
            auto_action=alert.auto_action,
        )
    if alert.auto_action != "stop_service":
        return
    if alert.detector not in effective_allowed:
        return  # gated — operator has not opted this detector into auto-stop
    if log_dir.is_dir():
        emit_event(
            log_dir,
            "monitor_auto_stop_triggered",
            detector=alert.detector,
            host=host,
        )
    if host is None:
        _call_local_stop(project)
    else:
        try:
            run_remote_command(
                host,
                f"agent-runner stop --config ~/.agent-runner/{project}/agent-runner.toml",
                timeout=30,
            )
        except MonitorRemoteError as e:
            if log_dir.is_dir():
                emit_event(
                    log_dir,
                    "monitor_auto_stop_failed",
                    detector=alert.detector,
                    host=host,
                    error=e.stderr,
                )
            # Continue rather than crash the monitor loop — the next poll
            # will retry naturally if the condition persists.
