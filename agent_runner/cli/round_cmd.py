"""round subcommand — runs one supervisor round (used by serve and systemd)."""

from __future__ import annotations

from agent_runner.cli.common import cfg_from_args
from agent_runner.runner import run_one_round


def add_parser(sub, parent) -> None:
    p = sub.add_parser("round", parents=[parent], help="Run one round and exit")
    p.add_argument(
        "--phase",
        type=str,
        default=None,
        metavar="NAME",
        help="Override phase for this round (must match a name in [phases]); "
        "does not mutate the rotation counter.",
    )
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    cfg = cfg_from_args(args)
    result = run_one_round(cfg, phase_override=args.phase)
    # Surface a real agent crash as a non-zero exit so serve's crash-loop breaker
    # (which keys on this subprocess's returncode) can count it. A grace-kill (agent
    # produced a result then lingered) or a timeout (long, not a short crash) is NOT
    # a crash → 0. Never returns the serve-reserved 78 / 75.
    crashed = result.exit_code != 0 and not result.killed_for_grace and not result.timed_out
    return 1 if crashed else 0
