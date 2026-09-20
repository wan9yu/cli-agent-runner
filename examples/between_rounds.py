"""Between-round file swap for a running ``agent-runner serve``.

Not a supervisor: does not spawn ``round``, does not sandbox the agent, and
must not auto-restart serve on give-up exits 78/75/70.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
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
_MIN_PROMPT_BYTES = 500
_FORBIDDEN_FIRST = frozenset({"-", " ", "\n", "\t", "\r"})


def atomic_replace(path: Path, text: str) -> None:
    """Write ``text`` over ``path`` via tmp + fsync + ``os.replace``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".swap-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def prompt_smoke_error(text: str) -> str | None:
    """Return a reason the round child would 78, or None if the body is usable."""
    if not text:
        return "empty"
    if text[0] in _FORBIDDEN_FIRST:
        return "bad first character"
    if len(text.encode("utf-8")) < _MIN_PROMPT_BYTES:
        return f"under {_MIN_PROMPT_BYTES} bytes"
    return None


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


def apply_queued_prompt(prompt_path: Path, queued: Path) -> None:
    text = queued.read_text(encoding="utf-8")
    err = prompt_smoke_error(text)
    if err is not None:
        raise ValueError(f"queued prompt would 78 ({err}): {queued}")
    atomic_replace(prompt_path, text)
    queued.unlink()


def follow_rounds(log_dir: Path, *, sleep_s: float = 0.5) -> Iterator[list[dict]]:
    """Yield newly completed JSONL objects forever (one batch per wake)."""
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
    p.add_argument("--prompt", type=Path, help="prompt file to replace between rounds")
    p.add_argument(
        "--queue",
        type=Path,
        help="if this file exists after round_end, swap it onto --prompt",
    )
    args = p.parse_args(argv)
    print(
        "between_rounds: watching"
        f" {events_path(args.log_dir)} (drive serve yourself; will not restart on 78/75/70)",
        file=sys.stderr,
    )
    for batch in follow_rounds(args.log_dir):
        kind = give_up_kind(batch)
        if kind is not None:
            print(f"between_rounds: give-up {kind}; not restarting", file=sys.stderr)
            return 78 if kind == "config_broken" else 1
        if args.prompt is None or args.queue is None:
            continue
        for ev in batch:
            if ev.get("event") != "round_end":
                continue
            if not args.queue.is_file():
                continue
            apply_queued_prompt(args.prompt, args.queue)
            print(f"between_rounds: swapped {args.queue} -> {args.prompt}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
