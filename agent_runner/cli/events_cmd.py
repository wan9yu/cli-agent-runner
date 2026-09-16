"""agent-runner events — event-stream observation verb.

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

from agent_runner import _notify, event_log
from agent_runner.events import _iter_parsed_lines, open_events_jsonl, parse_iso_ms

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
        rc = _replay_and_print(log_dir, kind_set, since, {})
        return rc if rc is not None else 0

    window = args.window if args.window != _WINDOW_DEFAULT_SENTINEL else 10
    return _query_events(log_dir, kind_set, window)


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


def _replay_and_print(
    log_dir: Path, kind_set: set[str], since: datetime, seed_out: dict[Path, int]
) -> int | None:
    """Replay the ``--since`` backlog (kind AND ts>=since filtered), seeding
    ``seed_out`` with each in-since file's exact-byte handoff. Returns ``1`` on
    an unreadable file (the caller exits), else ``None`` (the caller continues).
    Shared by the one-shot query path and the tail's pre-follow phase."""
    try:
        for line, _obj in event_log.replay_since(
            log_dir, event_log.month_of_datetime(since), seed_out=seed_out
        ):
            if _matches_since(line, kind_set, since):
                print(line, flush=True)
    except OSError as e:
        print(f"Error: events file unreadable: {e}", file=sys.stderr)
        return 1
    return None


def _query_events(log_dir: Path, kind_set: set[str], window: int) -> int:
    """One-shot: read current-month events.jsonl, filter, print last N."""
    events_file = event_log.current_month_file(log_dir)
    if not events_file.exists():
        return 0

    matches: list[str] = []
    try:
        with open_events_jsonl(events_file) as f:
            for line, evt in _iter_parsed_lines(f):
                if evt.get("event") in kind_set:
                    matches.append(line)
    except OSError as e:
        print(f"Error: events file unreadable: {e}", file=sys.stderr)
        return 1

    for line in matches[-window:]:
        print(line)
    return 0


def _tail_events(log_dir: Path, kind_set: set[str], since: datetime | None = None) -> int:
    """Streaming: emit each new matching event as it fires, waking on the FIFO
    doorbell (``_notify``) -- a ``ring()`` from ``events.emit`` lands within
    milliseconds -- falling back to a 1s poll tick when no doorbell fd is
    available. Blocks until SIGINT (KeyboardInterrupt). A filter/print wrapper
    over the shared :func:`event_log.follow` loop (also used by the monitor
    tailer), scoped to the newest two monthly files so rollover just falls out
    of the scope re-globbing every iteration.

    With ``since``, the backlog (``ts >= since``, across month files) is replayed
    first via :func:`event_log.replay_since` and the poll resumes at the exact
    byte the replay stopped on — no gap and no duplicate across the handoff.
    At-least-once overall: the caller resumes from the last ts it saw, so that
    one event may repeat.
    """

    def _handle_sigint(_signum, _frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, _handle_sigint)

    with _notify.open_listener(log_dir) as listener:
        # Pre-seed ALL in-scope (newest-2) files at EOF, THEN let replay_since
        # overwrite ONLY the in-since months with their exact-byte handoff --
        # so the out-of-scope PRIOR month keeps its EOF seed and is never
        # dumped as backlog (the critical fix over the old scalar rollover).
        seed = event_log.seed_at_eof(event_log.newest_month_files(log_dir, 2))
        try:
            if since is not None:
                rc = _replay_and_print(log_dir, kind_set, since, seed)
                if rc is not None:
                    return rc

            for line, obj in event_log.follow(
                log_dir, event_log.newest_scope(2), wake=listener, timeout_s=1.0, offsets=seed
            ):
                if obj.get("event") in kind_set:  # kind-only on the follow continuation
                    print(line, flush=True)
        except KeyboardInterrupt:
            return 0
