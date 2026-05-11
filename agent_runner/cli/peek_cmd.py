"""peek and watch subcommands — snapshot + auto-refresh."""

from __future__ import annotations

import os
import time
from pathlib import Path

from agent_runner import api
from agent_runner.cli.common import emit, fail


def add_parser(sub, parent) -> None:
    for verb, fn in (("peek", cmd_peek), ("watch", cmd_watch)):
        p = sub.add_parser(verb, parents=[parent],
                           help=f"{verb} project state with optional drill-down")
        p.add_argument("--round", type=int, default=None, help="Drill into round N")
        p.add_argument("--log", action="store_true", help="Include current round's log tail")
        p.add_argument("--events", type=int, default=None,
                       metavar="N", help="Include last N events")
        p.add_argument("--select", type=str, default=None,
                       help="Dot-path subtree to extract (e.g. system.disk_used_pct)")
        if verb == "watch":
            p.add_argument("--interval", type=int, default=2,
                           metavar="SECONDS", help="Refresh interval (default 2)")
        p.set_defaults(func=fn)


def cmd_peek(args) -> int:
    try:
        result = api.peek(Path.cwd(), round=args.round, select=args.select)
    except KeyError as e:
        return fail(str(e))
    except FileNotFoundError as e:
        return fail(f"config not found: {e}")
    emit(result, json_mode=getattr(args, "json", False))
    return 0


def cmd_watch(args) -> int:
    while True:
        os.system("clear")  # noqa: S605 — terminal control, not user input
        rc = cmd_peek(args)
        if rc != 0:
            return rc
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0
