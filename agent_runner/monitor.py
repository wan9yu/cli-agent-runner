"""Monitor — anomaly detectors over events + metrics + log tails.

Phase 2 ships 9 detectors. Two trigger ``auto_action="stop_service"``:
  * oauth_fail  — auth pattern in short-exit logs (retrying burns API quota)
  * disk_critical — disk_used_pct > 95% (writing more risks corruption)

The detectors are pure functions; the loop, ssh fetch, and auto-stop wiring
live further down (Tasks 3.2 / 3.3).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from agent_runner.api_types import Alert
from agent_runner.events import now_iso_ms

KNOWN_ALERT_KINDS: frozenset[str] = frozenset({
    "timeout_rate", "hung", "orphan_chain",
    "disk_warning", "disk_critical", "mem_pressure",
    "smoke_fail_rate", "oauth_fail", "network_fail",
})

SHORT_EXIT_THRESHOLD_S = 60

_AUTH_PATTERNS = re.compile(
    r"\b(oauth|unauthorized|401|api[_ ]key|"
    r"auth(entication)?[_ -]?(failed|error|expired)|session.*expired)\b",
    re.IGNORECASE,
)
_NETWORK_PATTERNS = re.compile(
    r"\b(connection refused|econnrefused|dns|"
    r"name or service not known|connect(ion)? timed out|"
    r"nodename nor servname|network unreachable|"
    r"50[023] (service unavailable|bad gateway|gateway timeout)|"
    r"connection reset)\b",
    re.IGNORECASE,
)


def _alert(detector: str, severity: str, message: str, context: dict[str, Any],
           auto_action: str = "none") -> Alert:
    assert detector in KNOWN_ALERT_KINDS, f"unknown alert kind: {detector!r}"
    return Alert(
        severity=severity, detector=detector, message=message,
        context=context, ts=now_iso_ms(), auto_action=auto_action,
    )


def _last_n_round_exits(events: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    exits = [e for e in events if e.get("event") == "agent_exit"]
    return exits[-n:]


def detect_timeout_rate(events: list[dict[str, Any]], *, window: int = 10,
                        threshold: float = 0.2) -> Alert | None:
    recent = _last_n_round_exits(events, window)
    if len(recent) < window:
        return None
    timed = sum(1 for e in recent if e.get("timed_out"))
    rate = timed / len(recent)
    if rate < threshold:
        return None
    return _alert(
        "timeout_rate", "warning",
        f"{timed}/{len(recent)} recent rounds timed out (>{threshold:.0%})",
        {"rate": rate, "threshold": threshold, "window": window},
    )


def detect_hung(events: list[dict[str, Any]], *, now: datetime,
                factor: float = 1.5, round_timeout_s: int = 1800) -> Alert | None:
    """A round_start without a matching round_end after round_timeout_s * factor."""
    open_rounds: dict[int, str] = {}
    for e in events:
        kind = e.get("event")
        rn = e.get("round_num")
        if kind == "round_start" and rn is not None:
            open_rounds[rn] = e["ts"]
        elif kind == "round_end" and rn in open_rounds:
            del open_rounds[rn]
    for rn, started_ts in open_rounds.items():
        started = datetime.fromisoformat(started_ts.replace("Z", "+00:00"))
        elapsed = (now - started).total_seconds()
        if elapsed > round_timeout_s * factor:
            return _alert(
                "hung", "warning",
                f"Round {rn} started {elapsed:.0f}s ago with no round_end",
                {"round_num": rn, "elapsed_s": elapsed,
                 "threshold_s": round_timeout_s * factor},
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
            "orphan_chain", "warning",
            f"{streak} consecutive rounds with orphan_stashed (>= {threshold})",
            {"streak": streak, "threshold": threshold,
             "last_round": last_round_with_orphan},
        )
    return None


def _latest(metrics: list[dict[str, Any]], key: str) -> Any:
    for m in reversed(metrics):
        if key in m:
            return m[key]
    return None


def detect_disk_warning(metrics: list[dict[str, Any]], *,
                        threshold_pct: float = 90.0) -> Alert | None:
    val = _latest(metrics, "disk_used_pct")
    if val is None or val < threshold_pct:
        return None
    if val >= 95.0:  # leave the >=95 case to detect_disk_critical
        return None
    return _alert(
        "disk_warning", "warning",
        f"disk_used_pct {val} >= {threshold_pct}",
        {"value": val, "threshold": threshold_pct,
         "hint": "Free space soon — clean ~/.agent-runner/<project>/logs/"},
    )


def detect_disk_critical(metrics: list[dict[str, Any]], *,
                         threshold_pct: float = 95.0) -> Alert | None:
    val = _latest(metrics, "disk_used_pct")
    if val is None or val < threshold_pct:
        return None
    return _alert(
        "disk_critical", "critical",
        f"disk_used_pct {val} >= {threshold_pct} — auto-stopping service",
        {"value": val, "threshold": threshold_pct,
         "hint": "Stop and clean disk before resuming"},
        auto_action="stop_service",
    )


def detect_mem_pressure(metrics: list[dict[str, Any]], *,
                        threshold_mb: int = 200) -> Alert | None:
    val = _latest(metrics, "mem_available_mb")
    if val is None or val >= threshold_mb:
        return None
    return _alert(
        "mem_pressure", "warning",
        f"mem_available_mb {val} < {threshold_mb}",
        {"value": val, "threshold": threshold_mb,
         "hint": "Investigate memory leak or move to a larger host"},
    )


def detect_smoke_fail_rate(events: list[dict[str, Any]], *,
                           window: int = 10, threshold: float = 0.1) -> Alert | None:
    ends = [e for e in events if e.get("event") == "round_end"]
    if len(ends) < window:
        return None
    recent_round_nums = [e.get("round_num") for e in ends[-window:]]
    fails = sum(
        1 for e in events
        if e.get("event") == "smoke_check_failed" and e.get("round_num") in recent_round_nums
    )
    rate = fails / window
    if rate < threshold:
        return None
    return _alert(
        "smoke_fail_rate", "warning",
        f"{fails}/{window} recent rounds had smoke_check_failed",
        {"rate": rate, "threshold": threshold,
         "hint": "Inspect events.jsonl for failure reasons"},
    )


def _short_exit_with_pattern(events: list[dict[str, Any]],
                             log_tails: dict[int, str],
                             pattern: re.Pattern[str], window: int) -> tuple[int, int]:
    recent = _last_n_round_exits(events, window)
    matches = 0
    for e in recent:
        rn = e.get("round_num")
        dur = e.get("duration_s") or 0.0
        exit_code = e.get("exit_code", 0)
        timed_out = e.get("timed_out", False)
        if dur < SHORT_EXIT_THRESHOLD_S and exit_code != 0 and not timed_out:
            tail = log_tails.get(rn, "")
            if pattern.search(tail):
                matches += 1
    return matches, len(recent)


def detect_oauth_fail(events: list[dict[str, Any]], log_tails: dict[int, str], *,
                      window: int = 10, threshold: float = 0.2) -> Alert | None:
    matches, total = _short_exit_with_pattern(events, log_tails, _AUTH_PATTERNS, window)
    if total < window or matches / total < threshold:
        return None
    return _alert(
        "oauth_fail", "critical",
        f"{matches}/{total} recent rounds short-exited with auth failure pattern",
        {"matches": matches, "window": total, "threshold": threshold,
         "hint": "Run `claude /login` on the supervisor host or refresh ANTHROPIC_API_KEY"},
        auto_action="stop_service",
    )


def detect_network_fail(events: list[dict[str, Any]], log_tails: dict[int, str], *,
                        window: int = 10, threshold: float = 0.2) -> Alert | None:
    matches, total = _short_exit_with_pattern(events, log_tails, _NETWORK_PATTERNS, window)
    if total < window or matches / total < threshold:
        return None
    return _alert(
        "network_fail", "warning",
        f"{matches}/{total} recent rounds short-exited with network error pattern",
        {"matches": matches, "window": total, "threshold": threshold,
         "hint": "Check upstream Anthropic status or local DNS / VPN"},
    )
