"""install / uninstall subcommands — manage systemd user units."""

from __future__ import annotations

import sys
from subprocess import CalledProcessError

from agent_runner import api
from agent_runner.cli.common import emit, fail, work_dir_from_args


def add_parser(sub, parent) -> None:
    p = sub.add_parser(
        "install", parents=[parent], help="Generate systemd user unit, enable + start"
    )
    p.add_argument(
        "--system",
        action="store_true",
        help=(
            "Install at system level (writes to /etc/systemd/system/,"
            " requires sudo, does not auto-start)"
        ),
    )
    p.add_argument(
        "--monitor",
        action="store_true",
        help="Also install monitor sidekick service for auto-stop on critical alerts",
    )
    p.set_defaults(func=cmd_install)

    u = sub.add_parser(
        "uninstall", parents=[parent], help="Stop, disable, and remove systemd user unit(s)"
    )
    u.set_defaults(func=cmd_uninstall)


def cmd_install(args) -> int:
    work_dir = work_dir_from_args(args)
    try:
        result = api.install(work_dir, system=args.system, with_monitor=args.monitor)
    except (FileNotFoundError, RuntimeError, CalledProcessError) as e:
        return fail(str(e))
    emit(result, json_mode=getattr(args, "json", False))
    if args.system and result.started is False:
        project = work_dir.name
        sys.stderr.write(f"next: systemctl start agent-runner@{project}\n")
    return 0


def cmd_uninstall(args) -> int:
    work_dir = work_dir_from_args(args)
    api.uninstall(work_dir)
    return 0
