"""Throttle state helpers — read events.jsonl tail for transient error state.

Internal module. Callers: runner.py (serve loop back-off), api.py (peek).
Separated from runner.py to satisfy the ouroboros defense: runner.py writes
events.jsonl but must never read it back (§3 module boundary invariant).
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from agent_runner.api_types import TransientErrorState


def _check_throttle_state(log_dir: Path) -> TransientErrorState | None:
    """Scan events.jsonl tail for latest unmatched transient error.

    Reads `transient_error_detected` / `transient_error_recovered` event names.
    Returns TransientErrorState if currently throttled (reset still in future,
    no matching recovered after). Restart-safe.
    """
    candidates = sorted(log_dir.glob("events-*.jsonl"))
    if not candidates:
        return None
    with candidates[-1].open() as f:
        tail = deque(f, maxlen=100)
    events: list[dict[str, Any]] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    latest_detected: dict[str, Any] | None = None
    for ev in reversed(events):
        kind = ev.get("event")
        if kind == "transient_error_recovered":
            return None
        if kind == "transient_error_detected":
            latest_detected = ev
            break

    if latest_detected is None:
        return None
    reset_at = int(latest_detected.get("reset_at_epoch", 0))
    if reset_at <= time.time():
        return None  # Reset already passed without recovery emit; treat as recovered

    classification = str(latest_detected.get("classification", "rate_limit_account"))

    return TransientErrorState(
        reset_at_epoch=reset_at,
        classification=classification,
        agent=str(latest_detected.get("agent", "unknown")),
        since_round=int(latest_detected.get("round_num", 0)),
    )
