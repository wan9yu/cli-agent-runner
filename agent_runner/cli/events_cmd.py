"""agent-runner events — event-stream observation verb (0.1.34+).

One-shot (--window N) or streaming (--tail) query against events.jsonl.
JSON Lines output (one JSON object per line, no pretty-print).

Current-month scope only. Tail mode follows month rollover via per-poll glob.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Sentinel for "user did not explicitly set --window" so we can detect
# --window + --tail combinations. argparse mutually-exclusive group would
# be cleaner but argparse doesn't support "exclusive only when X has value Y".
_WINDOW_DEFAULT_SENTINEL = -1


def _positive_int(s: str) -> int:
    """Parse positive integer (duplicate of peek_cmd._positive_int; KISS)."""
    try:
        n = int(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expects positive int, got {s!r}") from e
    if n <= 0:
        raise argparse.ArgumentTypeError(f"expects positive int (> 0), got {n}")
    return n


def _parse_kinds(raw: str) -> set[str]:
    """Parse comma-separated kinds; strip whitespace; reject empty."""
    parts = [k.strip() for k in (raw or "").split(",") if k.strip()]
    return set(parts)


def add_parser(sub, parent) -> None:
    p = sub.add_parser(
        "events",
        parents=[parent],
        help="Query / stream events from events.jsonl by kind",
    )
    p.add_argument(
        "--kind",
        type=str,
        required=True,
        metavar="K[,K2,...]",
        help="Comma-separated event kinds (OR-filtered). At least one required.",
    )
    p.add_argument(
        "--window",
        type=_positive_int,
        default=_WINDOW_DEFAULT_SENTINEL,
        metavar="N",
        help="One-shot mode: emit last N matching events (default 10).",
    )
    p.add_argument(
        "--tail",
        action="store_true",
        help=("Streaming mode: emit each new matching event as it fires (blocks until SIGINT)."),
    )
    p.set_defaults(func=cmd_events)


def _resolve_log_dir(args) -> Path:
    """Resolve log_dir from --config (used by both cmd_events and tests)."""
    if getattr(args, "_log_dir_override", None) is not None:
        return args._log_dir_override
    from agent_runner.cli.common import work_dir_from_args
    from agent_runner.config import load_config

    cfg = load_config(work_dir_from_args(args) / "agent-runner.toml")
    return cfg.runtime.log_dir


def cmd_events(args) -> int:
    kind_set = _parse_kinds(args.kind)
    if not kind_set:
        print(
            "Error: --kind requires at least one non-empty event kind",
            file=sys.stderr,
        )
        return 2

    window_explicit = getattr(args, "_window_explicit", False) or (
        args.window != _WINDOW_DEFAULT_SENTINEL
    )
    if args.tail and window_explicit:
        print(
            "Error: --window and --tail are mutually exclusive",
            file=sys.stderr,
        )
        return 2

    try:
        log_dir = _resolve_log_dir(args)
    except FileNotFoundError as e:
        print(f"Error: config not found: {e}", file=sys.stderr)
        return 1

    if args.tail:
        return _tail_events(log_dir, kind_set)

    window = args.window if args.window != _WINDOW_DEFAULT_SENTINEL else 10
    return _query_events(log_dir, kind_set, window)


def _current_month_events_file(log_dir: Path) -> Path:
    month = datetime.now(UTC).strftime("%Y-%m")
    return log_dir / f"events-{month}.jsonl"


def _query_events(log_dir: Path, kind_set: set[str], window: int) -> int:
    """One-shot: read current-month events.jsonl, filter, print last N."""
    events_file = _current_month_events_file(log_dir)
    if not events_file.exists():
        return 0

    matches: list[str] = []
    try:
        with events_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if evt.get("event") in kind_set:
                    matches.append(line)
    except OSError as e:
        print(f"Error: events file unreadable: {e}", file=sys.stderr)
        return 1

    for line in matches[-window:]:
        print(line)
    return 0


def _tail_events(log_dir: Path, kind_set: set[str]) -> int:
    """Streaming: poll current-month events.jsonl at 1s interval; emit each
    new matching line as it fires. Blocks until SIGINT (KeyboardInterrupt).
    Follows month rollover via per-poll glob.
    """
    last_size = 0
    current_file: Path | None = None

    def _handle_sigint(_signum, _frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        while True:
            events_file = _current_month_events_file(log_dir)
            if events_file != current_file:
                # Month rollover OR first iteration: reset offset
                current_file = events_file
                last_size = events_file.stat().st_size if events_file.exists() else 0

            if events_file.exists():
                size = events_file.stat().st_size
                if size > last_size:
                    with events_file.open("r", encoding="utf-8") as f:
                        f.seek(last_size)
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                evt = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if evt.get("event") in kind_set:
                                print(line, flush=True)
                        # True EOF, not the size sampled above: a writer may have
                        # appended during the loop and those lines were printed.
                        last_size = f.tell()
                elif size < last_size:
                    # File truncated / rotated underneath us; reset
                    last_size = 0
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
