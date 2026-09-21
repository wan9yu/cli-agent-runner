"""Print ``config_digest`` changes from a running ``agent-runner serve``.

Not a supervisor: does not spawn ``round`` and must not auto-restart serve
on give-up exits 78/75/70.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path

GIVE_UP_KINDS = frozenset(
    {
        "config_broken",
        "crash_loop",
        "stalled_no_progress",
        "mem_loop_persistent",
    }
)


def iter_complete_objects(path: Path, offset: int) -> tuple[list[dict], int]:
    """Read complete JSONL objects from ``offset``. Partial last line is left unread."""
    if not path.is_file():
        return [], offset
    data = path.read_bytes()[offset:]
    if not data:
        return [], offset
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        if cut == -1:
            return [], offset
        data = data[: cut + 1]
    new_offset = offset + len(data)
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
    return out, new_offset


def events_path(log_dir: Path, month: str | None = None) -> Path:
    if month is None:
        month = time.strftime("%Y-%m", time.gmtime())
    return Path(log_dir) / f"events-{month}.jsonl"


def give_up_kind(events: list[dict]) -> str | None:
    for ev in events:
        kind = ev.get("event")
        if kind in GIVE_UP_KINDS:
            return str(kind)
    return None


def format_round_start(ev: dict) -> str | None:
    if ev.get("event") != "round_start":
        return None
    digest = str(ev.get("config_digest") or "")
    return (
        f"R{ev.get('round_num')} {digest[:12]} changed={ev.get('config_changed')}"
    )


def follow_rounds(log_dir: Path, *, sleep_s: float = 0.5) -> Iterator[list[dict]]:
    path = events_path(log_dir)
    offset = 0
    while True:
        batch, offset = iter_complete_objects(path, offset)
        if batch:
            yield batch
        time.sleep(sleep_s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--log-dir", type=Path, required=True)
    args = p.parse_args(argv)
    print(
        "digest_watch: watching"
        f" {events_path(args.log_dir)} (drive serve yourself; will not restart on 78/75/70)",
        file=sys.stderr,
    )
    for batch in follow_rounds(args.log_dir):
        kind = give_up_kind(batch)
        if kind is not None:
            print(f"digest_watch: give-up {kind}; not restarting", file=sys.stderr)
            return 78 if kind == "config_broken" else 1
        for ev in batch:
            line = format_round_start(ev)
            if line is not None:
                print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
