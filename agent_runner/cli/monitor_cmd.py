"""monitor subcommand — anomaly detection daemon (local or remote)."""

from __future__ import annotations

import json
import sys

from agent_runner import api, monitor
from agent_runner.cli.common import _to_jsonable, fail, work_dir_from_args


def add_parser(sub, parent) -> None:
    p = sub.add_parser(
        "monitor",
        parents=[parent],
        help="Anomaly detection daemon (anomaly mode, default) or live event stream (narrate mode)",
    )
    p.add_argument(
        "--host",
        type=str,
        default=None,
        metavar="SSH-ALIAS",
        help="Watch a remote agent-runner via ssh (anomaly mode only)",
    )
    p.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Poll interval (default 30s, 60s for remote)",
    )
    p.add_argument(
        "--mode",
        choices=["anomaly", "narrate", "events"],
        default="anomaly",
        help=(
            "anomaly (default): alert-only; narrate: human-readable event stream;"
            " events: JSONL event stream"
        ),
    )
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    mode = getattr(args, "mode", "anomaly")
    if mode in ("narrate", "events") and args.host is not None:
        return fail(f"--mode {mode} is local-only; remove --host or use --mode anomaly")

    if mode == "narrate":
        return _cmd_narrate(args)
    if mode == "events":
        return _cmd_events(args)
    return _cmd_anomaly(args)


def _cmd_anomaly(args) -> int:
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


def _cmd_events(args) -> int:
    """JSONL event stream — machine-readable variant of narrate."""
    import json as _json

    from agent_runner import api
    from agent_runner.cli.common import cfg_from_args

    cfg = cfg_from_args(args)
    log_dir = cfg.runtime.log_dir
    try:
        for evt in api.stream_events_jsonl(log_dir):
            print(_json.dumps(evt), flush=True)
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_narrate(args) -> int:
    from agent_runner.config import load_config

    work_dir = work_dir_from_args(args)
    cfg = load_config(work_dir / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        for line in api.narrate_events(log_dir):
            print(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        return 0
    return 0
