"""monitor subcommand — anomaly detection daemon (local or remote)."""

from __future__ import annotations

import json
import sys

from agent_runner import api, monitor
from agent_runner.cli.common import _to_jsonable, fail, work_dir_from_args


def add_parser(sub, parent) -> None:
    p = sub.add_parser(
        "monitor", parents=[parent], help="Anomaly detection daemon (local or remote via --host)"
    )
    p.add_argument(
        "--host",
        type=str,
        default=None,
        metavar="SSH-ALIAS",
        help="Watch a remote agent-runner via ssh",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Poll interval (default 30s, 60s for remote)",
    )
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    interval = args.interval if args.interval is not None else (60 if args.host else 30)
    json_mode = getattr(args, "json", False)
    try:
        work_dir = work_dir_from_args(args)
        for alert in api.monitor_loop(work_dir, host=args.host, interval_s=interval):
            if json_mode:
                print(json.dumps(_to_jsonable(alert)))
                sys.stdout.flush()
            else:
                tag = {"info": "[OK]", "warning": "[WARN]", "critical": "[CRIT]"}[alert.severity]
                print(f"{tag} {alert.detector} — {alert.message}")
                sys.stdout.flush()
    except KeyboardInterrupt:
        return 0
    except monitor.MonitorRemoteError as e:
        return fail(f"cannot reach {e.host!r} via ssh: {e.stderr}")
    return 0
