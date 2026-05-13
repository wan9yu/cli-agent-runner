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
    run_one_round(cfg, phase_override=args.phase)
    return 0
