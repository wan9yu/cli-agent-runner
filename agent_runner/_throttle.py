"""Throttle state helpers — read events.jsonl tail for rate-limit state.

Internal module. Callers: runner.py (serve loop back-off), api.py (peek).
Separated from runner.py to satisfy the ouroboros defense: runner.py writes
events.jsonl but must never read it back (§3 module boundary invariant).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent_runner.api_types import ThrottleState


def _check_throttle_state(log_dir: Path) -> ThrottleState | None:
    """Scan events.jsonl tail for latest unmatched rate_limit_rejected.

    Returns ThrottleState if currently throttled (reset still in future,
    no later rate_limit_recovered emitted), else None. Restart-safe:
    state comes from disk, not memory.
    """
    candidates = sorted(log_dir.glob("events-*.jsonl"))
    if not candidates:
        return None
    raw_events: list[dict[str, Any]] = []
    with candidates[-1].open() as f:
        for line in f.readlines()[-100:]:
            line = line.strip()
            if not line:
                continue
            try:
                raw_events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    latest_rejected: dict[str, Any] | None = None
    for ev in reversed(raw_events):
        kind = ev.get("kind") or ev.get("event")
        if kind == "rate_limit_recovered":
            return None  # Already recovered; no active throttle
        if kind == "rate_limit_rejected":
            latest_rejected = ev
            break
    if latest_rejected is None:
        return None
    reset_at = int(latest_rejected.get("reset_at_epoch", 0))
    if reset_at <= time.time():
        return None  # Reset already passed without recovery emit; treat as recovered
    return ThrottleState(
        reset_at_epoch=reset_at,
        limit_type=str(latest_rejected.get("limit_type", "unknown")),
        agent=str(latest_rejected.get("agent", "unknown")),
        since_round=int(latest_rejected.get("round_num", 0)),
    )
