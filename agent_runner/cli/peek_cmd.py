"""peek and watch subcommands — snapshot + auto-refresh."""

from __future__ import annotations

import argparse
import sys
import time

from agent_runner import api
from agent_runner.cli.common import emit, fail, work_dir_from_args


def _round_arg(s: str) -> int | str:
    if s == "latest":
        return s
    try:
        return int(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"--round expects int or 'latest', got {s!r}") from e


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
            help="Dot-path subtree to extract (e.g. system.disk_used_pct)",
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


def cmd_peek(args) -> int:
    try:
        result = api.peek(
            work_dir_from_args(args),
            round=args.round,
            log=args.log,
            events=args.events,
            select=args.select,
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
