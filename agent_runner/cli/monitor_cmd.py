"""monitor subcommand — anomaly detection, event stream (local or relayed), HTTP page."""

from __future__ import annotations

import json
import sys

from agent_runner import api, monitor
from agent_runner.cli.common import _to_jsonable, fail, work_dir_from_args

_SEVERITY_TAGS = {"info": "[OK]", "warning": "[WARN]", "critical": "[CRIT]"}


def add_parser(sub, parent) -> None:
    p = sub.add_parser(
        "monitor",
        parents=[parent],
        help="Anomaly detection, narrate/events stream, or HTTP progress page",
    )
    p.add_argument(
        "--host",
        type=str,
        default=None,
        metavar="SSH-ALIAS",
        help=(
            "Remote ssh alias — supported with --mode events only: agent-runner "
            "manages the ssh, resumes with --since after a drop, and kills the "
            "ssh process group on exit. Detection modes run on the host itself."
        ),
    )
    p.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Poll interval (default 30s)",
    )
    p.add_argument(
        "--kind",
        type=str,
        default=None,
        metavar="K[,K2,...]",
        help=(
            "Event kinds to relay (--host --mode events only). Default: every "
            "kind this client knows — built-ins plus locally installed plugin "
            "kinds. A kind that exists only on the remote must be named here."
        ),
    )
    p.add_argument(
        "--mode",
        choices=["anomaly", "narrate", "events", "http"],
        default="anomaly",
        help=(
            "anomaly (default): alert-only; narrate: human-readable event stream;"
            " events: JSONL event stream; http: browser progress page"
        ),
    )
    p.add_argument(
        "--port",
        type=int,
        default=8765,
        metavar="PORT",
        help="HTTP port for --mode http (default 8765, local-only)",
    )
    p.add_argument(
        "--remote-config",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Config path ON THE REMOTE HOST for the relayed events command "
            "(--host --mode events only). Default: omit --config entirely, so "
            "the remote resolves ./agent-runner.toml in the ssh landing directory."
        ),
    )
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    mode = getattr(args, "mode", "anomaly")
    host = getattr(args, "host", None)
    remote_only = [
        f"--{name}"
        for name in ("kind", "remote-config")
        if getattr(args, name.replace("-", "_"), None) is not None
    ]
    if host is not None and mode != "events":
        return fail(str(monitor.MonitorRemoteUnsupportedError(host, mode)))
    if remote_only and (host is None or mode != "events"):
        return fail(f"{', '.join(remote_only)} applies to --host --mode events only")

    if mode == "narrate":
        return _cmd_narrate(args)
    if mode == "events":
        return _cmd_events(args)
    if mode == "http":
        return _cmd_http(args)
    return _cmd_anomaly(args)


def _cmd_anomaly(args) -> int:
    interval = args.interval if args.interval is not None else 30
    json_mode = getattr(args, "json", False)
    try:
        work_dir = work_dir_from_args(args)
        # No host: cmd() has already rejected --host for every detection mode.
        for alert in api.monitor_loop(work_dir, interval_s=interval):
            if json_mode:
                print(json.dumps(_to_jsonable(alert)))
                sys.stdout.flush()
            else:
                tag = _SEVERITY_TAGS.get(alert.severity, f"[{alert.severity.upper()}]")
                print(f"{tag} {alert.detector} — {alert.message}")
                sys.stdout.flush()
    except KeyboardInterrupt:
        return 0
    return 0


def _cmd_events(args) -> int:
    """JSONL event stream — a local tail, or a managed ssh relay under --host.

    Both variants read the CLIENT's config: locally for the log dir to tail,
    remotely for where the relay writes its own link telemetry (blip / give-up)
    and for the reconnect deadline.
    """
    from agent_runner.cli.common import cfg_from_args

    cfg = cfg_from_args(args)
    log_dir = cfg.runtime.log_dir

    host = getattr(args, "host", None)
    if host is not None:
        raw_kinds = getattr(args, "kind", None)
        kinds = [k.strip() for k in raw_kinds.split(",") if k.strip()] if raw_kinds else None
        return api.relay_remote_events(
            host,
            log_dir=log_dir,
            kinds=kinds,
            remote_config=getattr(args, "remote_config", None),
            failure_tolerance_s=cfg.monitor.remote_failure_tolerance_s,
        )

    try:
        for evt in api.stream_events_jsonl(log_dir):
            print(json.dumps(evt), flush=True)
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_http(args) -> int:
    """HTTP progress endpoint — browser-friendly live progress view."""
    from agent_runner.cli.common import cfg_from_args
    from agent_runner.http_progress import serve_http_progress

    cfg = cfg_from_args(args)
    return serve_http_progress(cfg.runtime.log_dir, cfg.runtime.narrative_file, port=args.port)


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
