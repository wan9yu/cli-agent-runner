"""peek and watch subcommands — snapshot + auto-refresh."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from agent_runner import api
from agent_runner.cli.common import emit, fail, work_dir_from_args
from agent_runner.config import load_config


def _round_arg(s: str) -> int | str:
    if s == "latest":
        return s
    try:
        return int(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"--round expects int or 'latest', got {s!r}") from e


def _positive_int(s: str) -> int:
    try:
        n = int(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"expects positive int, got {s!r}") from e
    if n <= 0:
        raise argparse.ArgumentTypeError(f"expects positive int (> 0), got {n}")
    return n


def add_parser(sub, parent) -> None:
    for verb, fn in (("peek", cmd_peek), ("watch", cmd_watch)):
        p = sub.add_parser(
            verb, parents=[parent], help=f"{verb} project state with optional drill-down"
        )
        p.add_argument(
            "--round",
            type=_round_arg,
            default=None,
            metavar="N",
            help="Drill into round N (int or 'latest')",
        )
        p.add_argument("--log", action="store_true", help="Include current round's log tail")
        p.add_argument(
            "--events", type=int, default=None, metavar="N", help="Include last N events"
        )
        p.add_argument(
            "--select",
            type=str,
            default=None,
            help=(
                "Selector: 'events.<kind>' queries events.jsonl (current month); "
                "dot-path (e.g. system.disk_used_pct) extracts a subtree from peek state."
            ),
        )
        p.add_argument(
            "--window",
            type=_positive_int,
            default=10,
            metavar="N",
            help="Max entries returned by --select events.<kind> (default 10, must be > 0).",
        )
        if verb == "watch":
            p.add_argument(
                "--interval",
                type=int,
                default=2,
                metavar="SECONDS",
                help="Refresh interval (default 2)",
            )
        p.set_defaults(func=fn)


def _run_events_select(
    log_dir: Path, *, kind: str, window: int, month_tag: str | None = None
) -> list[dict]:
    """Read current-month events.jsonl and return last ``window`` events of ``kind``.

    Only the current month's file is scanned (documented limitation).
    Returns an empty list when the file is missing or no matching events found.
    """
    if month_tag is None:
        month_tag = datetime.now(UTC).strftime("%Y-%m")
    events_file = log_dir / f"events-{month_tag}.jsonl"
    if not events_file.exists():
        return []

    matches: list[dict] = []
    with events_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("event") == kind:
                matches.append(evt)

    return matches[-window:]


def cmd_peek(args) -> int:
    select = args.select
    # Intercept events.<kind> selector before passing to regular peek logic.
    if select is not None and select.startswith("events."):
        kind = select[len("events.") :]
        if not kind:
            print("Error: --select events.<kind> requires a non-empty kind", file=sys.stderr)
            return 2
        try:
            cfg = load_config(work_dir_from_args(args) / "agent-runner.toml")
        except FileNotFoundError as e:
            return fail(f"config not found: {e}")
        log_dir = cfg.runtime.log_dir
        window = getattr(args, "window", 10) or 10
        matches = _run_events_select(log_dir, kind=kind, window=window)
        if getattr(args, "json", False):
            print(json.dumps(matches, default=str))
        else:
            for m in matches:
                print(json.dumps(m, default=str))
        return 0

    try:
        result = api.peek(
            work_dir_from_args(args),
            round=args.round,
            log=args.log,
            events=args.events,
            select=select,
        )
    except KeyError as e:
        return fail(str(e))
    except FileNotFoundError as e:
        return fail(f"config not found: {e}")
    emit(result, json_mode=getattr(args, "json", False))
    return 0


def cmd_watch(args) -> int:
    while True:
        sys.stdout.write("\x1b[2J\x1b[H")
        rc = cmd_peek(args)
        if rc != 0:
            return rc
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0
