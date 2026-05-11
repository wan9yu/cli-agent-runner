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
    }
)


def _now_ms_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def emit(log_dir: Path, kind: str, **fields: Any) -> None:
    """Append one event line to events-YYYY-MM.jsonl (UTC). File auto-created."""
    if kind not in KNOWN_EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind!r}")
    log_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    month = now.strftime("%Y-%m")
    ts = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    path = log_dir / f"events-{month}.jsonl"
    payload = {"ts": ts, "event": kind, **fields}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
