"""agent-runner events — event-stream observation verb (0.1.34+).

One-shot (--window N) or streaming (--tail) query against events.jsonl.
JSON Lines output (one JSON object per line, no pretty-print).

Current-month scope only, except under ``--since <ISO ts>``, which replays every
matching event with ``ts >= since`` across month files (and then follows, under
``--tail``). That replay is at-least-once by contract: a client resumes by
passing the last ts it saw, so the boundary event may arrive twice — no event is
ever silently lost across a dropped connection.

Tail mode follows month rollover via per-poll glob.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent_runner.clock import SYSTEM_CLOCK
from agent_runner.events import open_events_jsonl, parse_iso_ms

# Sentinel for "user did not explicitly set --window" so we can detect
# --window + --tail combinations. argparse mutually-exclusive group would
# be cleaner but argparse doesn't support "exclusive only when X has value Y".
_WINDOW_DEFAULT_SENTINEL = -1


def _positive_int(s: str) -> int:
    """Parse positive integer for the --window arg."""
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
    p.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="ISO-TS",
        help=(
            "Replay every matching event with ts >= this ISO-8601 timestamp "
            "(e.g. 2026-07-27T10:00:00.000Z), across month files, then keep "
            "streaming if --tail. At-least-once: resume by passing the last ts "
            "you saw — that event may repeat, none is lost. Excludes --window."
        ),
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

    since_raw = getattr(args, "since", None)
    since: datetime | None = None
    if since_raw is not None:
        if window_explicit:
            print(
                "Error: --window and --since are mutually exclusive",
                file=sys.stderr,
            )
            return 2
        try:
            since = parse_iso_ms(since_raw)
        except ValueError:
            print(
                f"Error: --since expects an ISO-8601 timestamp "
                f"(e.g. 2026-07-27T10:00:00.000Z), got {since_raw!r}",
                file=sys.stderr,
            )
            return 2
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)

    try:
        log_dir = _resolve_log_dir(args)
    except FileNotFoundError as e:
        print(f"Error: config not found: {e}", file=sys.stderr)
        return 1

    if args.tail:
        return _tail_events(log_dir, kind_set, since=since)

    if since is not None:
        try:
            _replay_since(log_dir, kind_set, since)  # offset only matters to --tail
        except OSError as e:
            print(f"Error: events file unreadable: {e}", file=sys.stderr)
            return 1
        return 0

    window = args.window if args.window != _WINDOW_DEFAULT_SENTINEL else 10
    return _query_events(log_dir, kind_set, window)


def _current_month_events_file(log_dir: Path) -> Path:
    month = SYSTEM_CLOCK.now_utc().strftime("%Y-%m")
    return log_dir / f"events-{month}.jsonl"


def _month_of(events_file: Path) -> str:
    """The YYYY-MM embedded in an ``events-YYYY-MM.jsonl`` filename."""
    return events_file.name[len("events-") : -len(".jsonl")]


def _matches_since(line: str, kind_set: set[str], since: datetime) -> bool:
    """True if ``line`` is a matching kind whose ts is at or after ``since``.

    Malformed lines and lines carrying no parseable ``ts`` are skipped silently,
    matching the tolerance of the other parse loops in this module.
    """
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(evt, dict) or evt.get("event") not in kind_set:
        return False
    ts = evt.get("ts")
    if not isinstance(ts, str):
        return False
    try:
        parsed = parse_iso_ms(ts)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed >= since


def _replay_since(log_dir: Path, kind_set: set[str], since: datetime) -> tuple[Path, int]:
    """Emit every matching event with ``ts >= since``, oldest month file first.

    Not windowed: replay is the resume path, so it emits the whole backlog.

    Returns ``(current-month file, bytes consumed of it)``. That offset is the
    handoff point for ``--tail``: it is the true end-of-read position, so a line
    appended while the replay was running is picked up by the poll loop instead
    of being skipped, and no line is emitted in both phases.
    """
    since_month = since.astimezone(UTC).strftime("%Y-%m")
    current = _current_month_events_file(log_dir)
    offset = 0
    for path in sorted(log_dir.glob("events-*.jsonl")):
        if _month_of(path) < since_month:
            # Nothing to replay from a month that ended before `since`. The live
            # file lands here only when `since` names a future month — seed the
            # tail at its end rather than rewinding it to 0.
            if path == current:
                offset = path.stat().st_size
            continue
        with open_events_jsonl(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if _matches_since(line, kind_set, since):
                    print(line, flush=True)
            if path == current:
                offset = f.tell()
    return current, offset


def _query_events(log_dir: Path, kind_set: set[str], window: int) -> int:
    """One-shot: read current-month events.jsonl, filter, print last N."""
    events_file = _current_month_events_file(log_dir)
    if not events_file.exists():
        return 0

    matches: list[str] = []
    try:
        with open_events_jsonl(events_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(evt, dict) and evt.get("event") in kind_set:
                    matches.append(line)
    except OSError as e:
        print(f"Error: events file unreadable: {e}", file=sys.stderr)
        return 1

    for line in matches[-window:]:
        print(line)
    return 0


def _emit_new_lines(path: Path, start: int, kind_set: set[str]) -> int:
    """Print matching lines of ``path`` from byte ``start`` to true EOF; return EOF."""
    with open_events_jsonl(path) as f:
        f.seek(start)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(evt, dict) and evt.get("event") in kind_set:
                print(line, flush=True)
        return f.tell()


def _tail_events(log_dir: Path, kind_set: set[str], since: datetime | None = None) -> int:
    """Streaming: poll current-month events.jsonl at 1s interval; emit each
    new matching line as it fires. Blocks until SIGINT (KeyboardInterrupt).
    Follows month rollover via per-poll glob.

    With ``since``, the backlog (``ts >= since``, across month files) is replayed
    first and the poll resumes at the exact byte the replay stopped on — no gap
    and no duplicate across the handoff. At-least-once overall: the caller
    resumes from the last ts it saw, so that one event may repeat.
    """
    last_size = 0
    current_file: Path | None = None

    def _handle_sigint(_signum, _frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, _handle_sigint)

    if since is not None:
        try:
            current_file, last_size = _replay_since(log_dir, kind_set, since)
        except OSError as e:
            print(f"Error: events file unreadable: {e}", file=sys.stderr)
            return 1

    try:
        while True:
            events_file = _current_month_events_file(log_dir)
            if events_file != current_file:
                if current_file is not None and current_file.exists():
                    # Rollover: flush the old file's remaining tail before switching,
                    # then begin the new file at 0 (no line is lost across the boundary).
                    _emit_new_lines(current_file, last_size, kind_set)
                    last_size = 0
                elif current_file is None:
                    # True first iteration (no --since): skip the pre-existing backlog.
                    last_size = events_file.stat().st_size if events_file.exists() else 0
                current_file = events_file

            if events_file.exists():
                size = events_file.stat().st_size
                if size > last_size:
                    last_size = _emit_new_lines(events_file, last_size, kind_set)
                elif size < last_size:
                    # File truncated / rotated underneath us; reset
                    last_size = 0
            SYSTEM_CLOCK.sleep(1.0)
    except KeyboardInterrupt:
        return 0
