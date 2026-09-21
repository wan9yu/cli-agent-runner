"""Dump realized-round history from serve JSONL.

Not a replay simulator and not a supervisor: reads complete lines only,
prints JSON, never restarts serve.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Enough to reconstruct one lap: what the child hashed, whether it ended,
# and the mechanical verifier — not a discovery tree.
_KEEP = frozenset(
    {
        "round_start",
        "round_end",
        "agent_exit",
        "goal_check",
        "goal_assessment",
        "round_timeout_kill",
        "config_broken",
        "crash_loop",
        "stalled_no_progress",
        "mem_loop_persistent",
    }
)


def events_path(log_dir: Path, month: str | None = None) -> Path:
    if month is None:
        month = time.strftime("%Y-%m", time.gmtime())
    return Path(log_dir) / f"events-{month}.jsonl"


def iter_complete_objects(path: Path) -> list[dict]:
    """Complete JSONL objects; a partial last line is skipped."""
    if not path.is_file():
        return []
    data = path.read_bytes()
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        if cut == -1:
            return []
        data = data[: cut + 1]
    out: list[dict] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def project(ev: dict) -> dict | None:
    kind = ev.get("event")
    if kind not in _KEEP:
        return None
    row = {"event": kind}
    for key in (
        "round_num",
        "config_digest",
        "config_changed",
        "exit_code",
        "timed_out",
        "name",
        "ok",
        "phase",
    ):
        if key in ev:
            row[key] = ev[key]
    return row


def index_events(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for ev in events:
        row = project(ev)
        if row is not None:
            out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--log-dir", type=Path, required=True)
    p.add_argument("--month", help="YYYY-MM; default UTC now")
    args = p.parse_args(argv)
    path = events_path(args.log_dir, args.month)
    rows = index_events(iter_complete_objects(path))
    json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
