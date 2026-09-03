"""State-tree assembly: reading the local filesystem into a ``ProjectState``.

``LocalSource`` is the only ``StateSource`` implementation — detection always
runs on the supervised host, so every path is local. Detector *logic* lives in
``_monitor_detectors``; the plugin registry in ``_monitor_registry``; the
cycle-edge wiring (``run_all_detectors``/``on_alert``) in ``monitor.py``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agent_runner.api_types import ProjectState, ServiceMode, ServiceStatus, SystemMetrics
from agent_runner.builtin_plugins._constants import _TAIL_LINES
from agent_runner.context_store import read_json
from agent_runner.events import _iter_parsed_lines, iter_event_dicts, open_events_jsonl


class StateSource(Protocol):
    """The paths a poll reads. ``LocalSource`` is the only implementation:
    detection runs on the supervised host, so every path is local."""

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
    # Every caller (detectors, round_view.build_round_view via peek) assumes
    # the ``.get(...)`` shape -- iter_event_dicts already skips a bare
    # number/string/list line so it never reaches them.
    out: list[dict[str, Any]] = []
    for path in files:
        try:
            out.extend(iter_event_dicts(path))
        except OSError:
            continue
    return out


_MONITOR_EVENT_BUFFER = 20000
"""Rolling window for `_EventTail.buffer` — enough history for every built-in
detector's lookback (round-timeout chains, orphan streaks, dedup identity)
without holding a project's entire events history in memory forever."""


@dataclass
class _EventTail:
    """Per-poll byte-offset carry + bounded rolling event buffer, so a monitor poll
    parses only bytes appended since the previous poll instead of re-reading the
    entire events history every interval (as _tail_events_jsonl already carries)."""

    offsets: dict[Path, int] = field(default_factory=dict)
    buffer: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=_MONITOR_EVENT_BUFFER)
    )

    def read(self, files: list[Path]) -> list[dict[str, Any]]:
        for path in files:
            pos = self.offsets.get(path, 0)
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            if size < pos:
                pos = 0  # rotated/truncated underneath us
            if size == pos:
                continue
            with open_events_jsonl(path) as f:
                f.seek(pos)
                for _, parsed in _iter_parsed_lines(f):
                    self.buffer.append(parsed)
                self.offsets[path] = f.tell()
        return list(self.buffer)


_MAX_TAIL_FILES = 20
"""Newest round logs to tail per poll — detectors only inspect the last 10
round exits, and reading every historical log fully on every poll is
O(all-logs-ever) waste."""


def load_round_log_tails(rounds_dir: Path, *, tail_lines: int = _TAIL_LINES) -> dict[int, str]:
    """Tail the newest round logs as plain text (merged stdout+stderr).

    Window shares _TAIL_LINES with the plugin parsers: oauth/network
    detectors regex stderr text out of these tails, and a stderr burst must
    not evict the line they scan for (same eviction argument, rawer input).
    """
    tails: dict[int, str] = {}
    if not rounds_dir.is_dir():
        return tails

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    for f in sorted(rounds_dir.glob("R*-*.log"), key=_mtime)[-_MAX_TAIL_FILES:]:
        try:
            num = int(f.name.split("-", 1)[0][1:])
        except (ValueError, IndexError):
            continue
        try:
            from agent_runner.round_log import open_round_log  # lazy: avoids api<->monitor cycle

            with open_round_log(f) as fh:
                tails[num] = "".join(deque(fh, maxlen=tail_lines))
        except FileNotFoundError:
            continue
    return tails


def _latest_metric_dict(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return metrics[-1] if metrics else {}


def assemble_project_state(source: StateSource, *, project: str) -> ProjectState:
    metrics = parse_events_from_jsonl_files(source.metrics_files())
    status = read_json(source.status_path()) or {}
    orphan = read_json(source.orphan_path())
    latest = _latest_metric_dict(metrics)
    from agent_runner._throttle import _coerce_float, _coerce_int

    system = SystemMetrics(
        mem_total_mb=_coerce_int(latest.get("mem_total_mb"), 0),
        mem_available_mb=_coerce_int(latest.get("mem_available_mb"), 0),
        disk_used_pct=_coerce_float(latest.get("disk_used_pct"), 0.0),
        disk_free_gb=_coerce_float(latest.get("disk_free_gb"), 0.0),
        load_1m=latest.get("load_1m"),
        cpu_pct=latest.get("cpu_pct"),
        agent_process_count=_coerce_int(latest.get("agent_process_count"), 0),
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
