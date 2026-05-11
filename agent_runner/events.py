"""Structured event emitter — JSON Lines, monthly UTC naming."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KNOWN_EVENT_KINDS = frozenset(
    {
        "round_start",
        "agent_spawn",
        "agent_exit",
        "dirty_detected",
        "orphan_stashed",
        "orphan_idempotent_skip",
        "orphan_stash_failed",
        "round_timeout_kill",
        "sigterm_received",
        "status_recovered",
        "smoke_check_failed",
        "round_end",
        # Phase 2 monitor events
        "monitor_alert_emitted",  # any detector fired (info/warning)
        "monitor_auto_stop_triggered",  # critical alert triggered service stop
    }
)


def now_iso_ms() -> str:
    """UTC ISO-8601 timestamp with millisecond precision and trailing 'Z'.

    Shared helper — also used by metrics.py and runner.py for matching format.
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(log_dir: Path, kind: str, **fields: Any) -> None:
    """Append one event line to events-YYYY-MM.jsonl (UTC).

    Caller must ensure ``log_dir`` exists (runner.run_one_round does this once
    per round; tests use the ``tmp_log_dir`` fixture which creates it).
    """
    if kind not in KNOWN_EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind!r}")
    now = datetime.now(UTC)
    month = now.strftime("%Y-%m")
    ts = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    path = log_dir / f"events-{month}.jsonl"
    payload = {"ts": ts, "event": kind, **fields}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
