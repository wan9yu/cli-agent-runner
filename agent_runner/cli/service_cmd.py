"""service subcommands — start / stop / kill / restart / status."""

from __future__ import annotations

import time

from agent_runner import api
from agent_runner.cli.common import emit, info, work_dir_from_args


def add_parser(sub, parent) -> None:
    for verb, fn, help_text in (
        ("start", cmd_start, "Start the service"),
        ("stop", cmd_stop, "Graceful stop (waits for current round)"),
        ("kill", cmd_kill, "Force terminate (5s grace then SIGKILL)"),
        ("restart", cmd_restart, "stop + start (use --force for kill semantics)"),
        ("status", cmd_status, "Show current service state"),
    ):
        p = sub.add_parser(verb, parents=[parent], help=help_text)
        if verb == "restart":
            p.add_argument("--force", action="store_true", help="Use kill instead of stop")
        p.set_defaults(func=fn)


def cmd_start(args) -> int:
    emit(api.start(work_dir_from_args(args)), json_mode=getattr(args, "json", False))
    return 0


def cmd_stop(args) -> int:
    json_mode = getattr(args, "json", False)
    if not json_mode:
        info("stopping service...")
    t0 = time.monotonic()
    result = api.stop(work_dir_from_args(args))
    elapsed = time.monotonic() - t0
    if not json_mode:
        info(f"stopped ({elapsed:.1f}s)")
    emit(result, json_mode=json_mode)
    return 0


def cmd_kill(args) -> int:
    emit(api.kill(work_dir_from_args(args)), json_mode=getattr(args, "json", False))
    return 0


def cmd_restart(args) -> int:
    emit(
        api.restart(work_dir_from_args(args), force=args.force),
        json_mode=getattr(args, "json", False),
    )
    return 0


def cmd_status(args) -> int:
    emit(api.status(work_dir_from_args(args)), json_mode=getattr(args, "json", False))
    return 0
