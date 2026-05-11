"""Monitor — anomaly detectors over events + metrics + log tails.

Phase 2 ships 9 detectors. Two trigger ``auto_action="stop_service"``:
  * oauth_fail  — auth pattern in short-exit logs (retrying burns API quota)
  * disk_critical — disk_used_pct > 95% (writing more risks corruption)

The detectors are pure functions; the loop, ssh fetch, and auto-stop wiring
live further down (Tasks 3.2 / 3.3).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from agent_runner.api_types import (
    Alert,
    ProjectState,
    ServiceMode,
    ServiceStatus,
    SystemMetrics,
)
from agent_runner.context_store import read_json
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
    events = parse_events_from_jsonl_files(source.events_files())
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
    )
    # Phase 2: monitor doesn't compute current_round / recent_rounds —
    # those come from the api.peek path which has more context. Monitor
    # focuses on aggregate detectors over events.
    _ = events  # kept for future Phase 2 expansions
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
) -> list[Alert]:
    """Run all 9 detectors; returns alerts (empty = healthy)."""
    if now is None:
        now = datetime.now(UTC)
    candidates = [
        detect_timeout_rate(events),
        detect_hung(events, now=now, round_timeout_s=round_timeout_s),
        detect_orphan_chain(events),
        detect_disk_warning(metrics),
        detect_disk_critical(metrics),
        detect_mem_pressure(metrics),
        detect_smoke_fail_rate(events),
        detect_oauth_fail(events, log_tails),
        detect_network_fail(events, log_tails),
    ]
    return [a for a in candidates if a is not None]


# ---------------------------------------------------------------------------
# Remote source + auto-stop dispatch (Task 3.3)
# ---------------------------------------------------------------------------

import subprocess  # noqa: TID251, E402 — monitor needs ssh + local stop subprocess


def run_remote_command(host: str, cmd: str, *, timeout: int = 30) -> str:
    """Run a single shell command over ssh; returns stdout (raises on error)."""
    r = subprocess.run(
        ["ssh", host, cmd],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if r.returncode != 0:
        return r.stdout  # caller decides what to do
    return r.stdout


@dataclass(frozen=True)
class RemoteSource:
    """Mirrors LocalSource but fetches paths via ssh ls; reads via cat."""
    host: str
    project: str

    def _remote_log_dir(self) -> str:
        return f"~/.agent-runner/{self.project}/logs"

    def _list(self, glob: str) -> list[Path]:
        out = run_remote_command(self.host, f"ls -1 {self._remote_log_dir()}/{glob} 2>/dev/null")
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


def _call_local_stop(project: str, log_dir: Path) -> None:
    """Issue a graceful local stop via the in-process api (avoids subprocess hop).

    Late import to avoid circular dependency at module load time (api imports
    monitor for peek).
    """
    from agent_runner import api
    api.stop(project)


def on_alert(alert: Alert, *, project: str, host: str | None, log_dir: Path) -> None:
    """Act on a single alert. No-op for non-stop alerts; auto-stop fires real signal."""
    if alert.auto_action != "stop_service":
        return
    if host is None:
        _call_local_stop(project, log_dir)
    else:
        run_remote_command(
            host,
            f"agent-runner stop --config ~/.agent-runner/{project}/agent-runner.toml",
            timeout=30,
        )
