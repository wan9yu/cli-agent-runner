"""Pure anomaly detectors over events + metrics + log tails.

13 built-in detectors. Two trigger ``auto_action="stop_service"``:
  * oauth_fail  — agent-reported auth failures, or an auth pattern in
    short-exit logs (retrying burns API quota)
  * disk_critical — disk_used_pct > 95% (writing more risks corruption)

Every function here is pure: no filesystem I/O, no clock reads beyond the
``now``/``now`` parameters callers supply. State assembly (``_monitor_state``),
the plugin registry (``_monitor_registry``), and the cycle-edge wiring
(``run_all_detectors``/``on_alert`` in ``monitor.py``) live elsewhere.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_runner import host_health
from agent_runner._monitor_registry import _MONITOR_SELF_KINDS
from agent_runner.api_types import Alert
from agent_runner.clock import SYSTEM_CLOCK
from agent_runner.config import _DEFAULT_AUTH_PATTERNS, MonitorHostHealthConfig, PhaseOverride
from agent_runner.events import (
    AGENT_AUTH_ERROR_DETECTED,
    AGENT_EXIT,
    ANOMALY_REPETITIVE_TOOL,
    ORPHAN_STASHED,
    ROUND_DEFERRED,
    ROUND_END,
    ROUND_RESUMED,
    ROUND_START,
    SCHEDULE_PAUSED,
    SCHEDULE_RESUMED,
    TRANSIENT_ERROR_DETECTED,
    TRANSIENT_ERROR_RECOVERED,
    now_iso_ms,
    parse_iso_ms,
)

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
    exits = [e for e in events if e.get("event") == AGENT_EXIT]
    return exits[-n:]


def detect_timeout_rate(
    events: list[dict[str, Any]], *, window: int = 10, threshold: float = 0.2
) -> Alert | None:
    recent = _last_n_round_exits(events, window)
    if len(recent) < window:
        return None
    # A grace-kill sets timed_out too but is NOT a hung round (agent produced a
    # result then lingered) — exclude it so the rate reflects real timeouts.
    timed = sum(1 for e in recent if e.get("timed_out") and e.get("exit_cause") != "grace_kill")
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
    highest_rn: int | None = None
    for e in events:
        kind, rn = e.get("event"), e.get("round_num")
        if rn is None or kind not in (ROUND_START, ROUND_END):
            continue  # e.g. a plugin's pre-round event for a not-yet-started round
        if kind == ROUND_START:
            open_rounds[rn] = (e["ts"], e.get("phase"))
            highest_rn = rn if highest_rn is None else max(highest_rn, rn)
        else:
            open_rounds.pop(rn, None)
    # Serial rotation (§7): only the highest-numbered STARTED round (open or
    # closed) can be a live hang — a lower still-open round is a dropped
    # round_end from a crash that a later, now-closed round already supersedes.
    if highest_rn not in open_rounds:
        return None
    rn = highest_rn
    started_ts, phase = open_rounds[rn]
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
    rounds_in_order = [e for e in events if e.get("event") in (ROUND_END, ORPHAN_STASHED)]
    orphan_rounds = {
        e.get("round_num") for e in rounds_in_order if e.get("event") == ORPHAN_STASHED
    }
    streak = 0
    last_round_with_orphan: int | None = None
    for e in rounds_in_order:
        if e.get("event") == ORPHAN_STASHED:
            streak += 1
            last_round_with_orphan = e.get("round_num")
        elif e.get("event") == ROUND_END and e.get("round_num") not in orphan_rounds:
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


def _latest_two(metrics: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (current, previous) full metric dicts -- the last two history
    entries (or ``{}`` when shorter). The mem-pressure ladder needs a delta
    across samples, unlike the single-key lookback ``_latest`` gives the
    other host-health detectors."""
    cur = metrics[-1] if metrics else {}
    prev = metrics[-2] if len(metrics) >= 2 else {}
    return cur, prev


def detect_mem_pressure(
    metrics: list[dict[str, Any]], *, cfg: MonitorHostHealthConfig | None = None
) -> Alert | None:
    """Cache-poor-valid memory pressure via ``host_health``'s signal ladder
    (PSI -> swap-out rate -> combined-low -> unavailable). ``auto_action``
    stays ``"none"`` this release -- the graded admission lever is 0.3.

    When no tier has a usable reading, this does NOT silently trust
    ``mem_available_mb`` alone (the original bug) -- it emits a distinct
    ``mem_signal_unavailable`` warning instead.
    """
    cfg = cfg if cfg is not None else MonitorHostHealthConfig()
    cur, prev = _latest_two(metrics)
    if not cur:
        return None  # nothing sampled yet -- a startup grace period, not a bug
    pressure = host_health.memory_pressure(cur, prev, cfg)
    if pressure is not None:
        return _alert("mem_pressure", pressure.severity, pressure.message, pressure.context)
    if not host_health.signal_available(cur, prev):
        return _alert(
            "mem_signal_unavailable",
            "warning",
            "no cache-poor-valid memory signal available on this host (PSI "
            "unreadable, no swap-out history, MemFree unknown) -- mem_pressure "
            "cannot detect pressure here",
            {"mem_available_mb": cur.get("mem_available_mb")},
        )
    return None


def detect_mem_pressure_gate_inert(
    metrics: list[dict[str, Any]], *, cfg: MonitorHostHealthConfig | None = None
) -> Alert | None:
    """Fail-loud self-check: fires when a cache-poor-valid signal (PSI or
    swap-out rate) shows real pressure WHILE ``mem_available_mb`` stays at or
    above ``cfg.mem_avail_min_mb`` -- i.e. the configured gate is provably
    inert on this host. Does NOT fire on "MemAvailable >> MemFree" alone,
    which is true on every healthy warm-cache host."""
    cfg = cfg if cfg is not None else MonitorHostHealthConfig()
    cur, prev = _latest_two(metrics)
    if not host_health.configured_gate_inert(cur, prev, cfg):
        return None
    return _alert(
        "mem_pressure_gate_inert",
        "warning",
        f"mem_avail_min_mb={cfg.mem_avail_min_mb} cannot fire on this host: a "
        f"cache-poor-valid signal shows real pressure while mem_available_mb="
        f"{cur.get('mem_available_mb')} stays >= threshold",
        {
            "mem_avail_min_mb": cfg.mem_avail_min_mb,
            "mem_available_mb": cur.get("mem_available_mb"),
            "hint": "MemAvailable is inflated on this host -- lower mem_avail_min_mb "
            "won't help; rely on the mem_pressure alert itself instead",
        },
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
    """Auth-failure loop over the last ``window`` rounds, from two evidence paths.

    1. **Text heuristic** — an auth pattern in the round's log tail, gated on a
       short *nonzero* exit that did not time out. The gate is the false-positive
       shield: the tail is free text, and prose mentioning "401" in a round that
       exited cleanly is not an auth failure.
    2. **Structured** — the round carries an ``agent_auth_error_detected`` event,
       emitted by a per-CLI plugin that read the failure out of the agent's own
       output. That is certain, not inferred, so it needs no exit-code shield —
       and the shield would in fact hide it, since some agent CLIs exit 0 even
       when the provider rejected the credential.

    A round matching either path counts once; both paths share this window,
    threshold, and auto-stop.
    """
    pats = patterns or [re.compile(p, re.IGNORECASE) for p in _DEFAULT_AUTH_PATTERNS]
    recent = _last_n_round_exits(events, window)
    auth_rounds = {
        e.get("round_num")
        for e in events
        if e.get("event") == AGENT_AUTH_ERROR_DETECTED and e.get("round_num") is not None
    }
    matches = sum(
        1
        for e in recent
        if e.get("round_num") in auth_rounds
        or (
            (e.get("duration_s") or 0.0) < SHORT_EXIT_THRESHOLD_S
            and e.get("exit_code", 0) != 0
            and not e.get("timed_out", False)
            and any(p.search(log_tails.get(e.get("round_num"), "")) for p in pats)
        )
    )
    total = len(recent)
    if total < window or matches / total < threshold:
        return None
    return _alert(
        "oauth_fail",
        "critical",
        f"{matches}/{total} recent rounds failed auth (agent-reported or short-exit pattern)",
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
    events: list[dict[str, Any]], *, now: float | None = None, log_dir: Path | None = None
) -> Alert | None:
    """Fire warning alert if currently throttled (latest transient_error_detected
    has reset_at_epoch in future, no matching recovered after).

    Reads the SAME ladder-extended reset serve's loop-top gate / skip path / peek
    converge on (``agent_runner._throttle._events_derived_reset``) when ``log_dir``
    is given — ``run_all_detectors``'s real caller always supplies one; it stays
    optional only so a caller with no on-disk events dir degrades to the emitter's
    raw reset rather than raising."""
    if now is None:
        now = SYSTEM_CLOCK.epoch()
    for ev in reversed(events):
        kind = ev.get("event")
        if kind == TRANSIENT_ERROR_RECOVERED:
            return None
        if kind == TRANSIENT_ERROR_DETECTED:
            from agent_runner._throttle import _coerce_int, _events_derived_reset

            agent = str(ev.get("agent", "unknown"))
            classification = ev.get("classification", "unknown")
            reset = _coerce_int(ev.get("reset_at_epoch"), 0)
            if log_dir is not None:
                reset = _events_derived_reset(log_dir, agent, str(classification), reset)
            if reset > now:
                iso = datetime.fromtimestamp(reset, UTC).isoformat()
                return _alert(
                    "rate_limit_active",
                    "warning",
                    f"throttled until {iso} ({classification})",
                    {
                        "throttled_until_iso": iso,
                        "classification": classification,
                        "agent": agent,
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
        if e.get("event") == ANOMALY_REPETITIVE_TOOL and e.get("round_num", 0) >= window_start
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


def _latest_schedule_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest schedule_paused/schedule_resumed event by stream order, or None.

    Reverse-walk with early break — events grows unboundedly over a project's life."""
    for e in reversed(events):
        if e.get("event") in (SCHEDULE_PAUSED, SCHEDULE_RESUMED):
            return e
    return None


# Event kinds that, like schedule_paused/resumed, mark a live pause/defer window
# for detect_supervisor_stale's suppression check below. round_deferred (the
# memory-pressure admission gate, 0.2.14) joins schedule_paused here so a long
# defer is not mistaken for a dead supervisor. Kept separate from
# _latest_schedule_event/latest_schedule_state above, which peek's schedule
# display reads and must stay schedule-only.
_STALE_SUPPRESSING_PAUSE_KINDS = (SCHEDULE_PAUSED, ROUND_DEFERRED)
_STALE_SUPPRESSING_RESUME_KINDS = (SCHEDULE_RESUMED, ROUND_RESUMED)


def _latest_stale_pause_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest schedule_paused/round_deferred/*_resumed event by stream order, or
    None -- the suppression check for detect_supervisor_stale."""
    kinds = _STALE_SUPPRESSING_PAUSE_KINDS + _STALE_SUPPRESSING_RESUME_KINDS
    for e in reversed(events):
        if e.get("event") in kinds:
            return e
    return None


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
    supervised = [e for e in events if e.get("event") not in _MONITOR_SELF_KINDS]
    if stale_threshold_s <= 0 or not supervised:
        return None
    last_ts_str = max((e["ts"] for e in supervised if "ts" in e), default=None)
    if last_ts_str is None:
        return None
    age_s = (now - parse_iso_ms(last_ts_str)).total_seconds()
    if age_s <= stale_threshold_s:
        return None

    newest = _latest_stale_pause_event(events)
    if newest is not None and newest.get("event") in _STALE_SUPPRESSING_PAUSE_KINDS:
        # Suppress only while the pause/defer is plausibly still live: until its
        # announced resume_at plus one staleness window (round_deferred carries
        # no resume_at, so it always falls to the ts+8d horizon below). A
        # supervisor that died mid-pause therefore still alarms once that bound
        # passes.
        paused = newest
        bound = None
        try:
            resume_dt = parse_iso_ms(paused.get("resume_at") or "")
            if resume_dt.tzinfo is not None:
                bound = resume_dt
        except (ValueError, TypeError):
            bound = None
        if bound is None:
            # No usable resume_at (empty/unparseable/naive) → bound by the pause's
            # own timestamp + the 8-day resume horizon, so an always-paused config
            # is not a permanent silent-death blind spot.
            try:
                bound = parse_iso_ms(paused["ts"]) + timedelta(days=8)
            except (KeyError, ValueError, TypeError):
                return None  # truly no anchor → suppress conservatively
        if now <= bound + timedelta(seconds=stale_threshold_s):
            return None
    return _alert(
        "supervisor_stale",
        "warning",
        f"No events for {int(age_s)}s (threshold {stale_threshold_s}s) — "
        f"supervisor may be stuck or dead. Last event: {last_ts_str}.",
        {"age_s": int(age_s), "threshold_s": stale_threshold_s, "last_ts": last_ts_str},
    )


def latest_schedule_state(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Current schedule-pause state derived from the event stream.

    Returns None when there is no pause in effect (no schedule events, or the
    newest one is a resume). When paused, returns the paused indicator dict."""
    newest = _latest_schedule_event(events)
    if newest is None or newest.get("event") != SCHEDULE_PAUSED:
        return None
    return {
        "paused": True,
        "resume_at": newest.get("resume_at", ""),
        "active_window": newest.get("active_window", ""),
        "phase": newest.get("phase", ""),
    }
