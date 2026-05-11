"""CLI entry — argparse subcommand dispatcher.

Each subcommand lives in its own ``*_cmd.py`` file; this module just routes.
The ``--config`` and ``--json`` flags can be placed before OR after the
subcommand verb. To make this work without the subparser's defaults clobbering
values supplied to the main parser, the main parser owns the real defaults
while the subparser-shared parent declares the same flags with
``argparse.SUPPRESS`` so they only mutate the namespace when explicitly
supplied after the verb.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_runner.cli import (
    init_cmd,
    install_cmd,
    monitor_cmd,
    peek_cmd,
    round_cmd,
    serve_cmd,
    service_cmd,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-runner",
        description="Restart-on-exit supervisor for autonomous CLI agents.",
    )
    parser.add_argument(
        "--config", type=Path, default=Path("./agent-runner.toml"),
        help="Path to agent-runner.toml (default: ./agent-runner.toml)",
    )
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output (where supported)")

    # Parent parser shared by every subparser so the same flags can also be
    # placed AFTER the verb. SUPPRESS keeps the subparser from overwriting
    # values supplied to the main parser when the flag is omitted.
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command", required=False)

    init_cmd.add_parser(sub, parent)
    install_cmd.add_parser(sub, parent)
    service_cmd.add_parser(sub, parent)
    peek_cmd.add_parser(sub, parent)
    monitor_cmd.add_parser(sub, parent)
    serve_cmd.add_parser(sub, parent)
    round_cmd.add_parser(sub, parent)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
