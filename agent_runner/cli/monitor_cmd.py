"""monitor subcommand — anomaly detection daemon (local or remote)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_runner import api
from agent_runner.cli.common import _to_jsonable


def add_parser(sub, parent) -> None:
    p = sub.add_parser("monitor", parents=[parent],
                       help="Anomaly detection daemon (local or remote via --host)")
    p.add_argument("--host", type=str, default=None,
                   metavar="SSH-ALIAS", help="Watch a remote agent-runner via ssh")
    p.add_argument("--interval", type=int, default=30,
                   metavar="SECONDS", help="Poll interval (default 30s, 60s for remote)")
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    interval = args.interval if args.interval != 30 or args.host is None else 60
    json_mode = getattr(args, "json", False)
    try:
        for alert in api.monitor_loop(Path.cwd(), host=args.host, interval_s=interval):
            if json_mode:
                print(json.dumps(_to_jsonable(alert)))
                sys.stdout.flush()
            else:
                tag = {"info": "[OK]", "warning": "[WARN]", "critical": "[CRIT]"}[alert.severity]
                print(f"{tag} {alert.detector} — {alert.message}")
                sys.stdout.flush()
    except KeyboardInterrupt:
        return 0
    return 0
