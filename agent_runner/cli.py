"""CLI entry point — `agent-runner round` / `--status` / `--metrics`."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

from agent_runner import context_store
from agent_runner.config import load_config
from agent_runner.runner import run_one_round


def _print_status(log_dir: Path) -> int:
    status = context_store.read_status(log_dir)
    if status is None:
        print("no status file yet — has any round run?")
        return 0
    print(
        json.dumps(
            {
                "round_num": status.round_num,
                "running": status.running,
                "last_exit_code": status.last_exit_code,
                "last_duration_s": status.last_duration_s,
                "last_completed_at": status.last_completed_at,
                "current_phase": status.current_phase,
                "phase_index": status.phase_index,
            },
            indent=2,
        )
    )
    return 0


def _print_metrics(log_dir: Path, count: int) -> int:
    files = sorted(log_dir.glob("metrics-*.jsonl"))
    if not files:
        print("no metrics yet")
        return 0
    # deque(maxlen=N) keeps constant memory; the file iterator avoids loading
    # the whole month-file into a list (relevant once it accumulates ~tens of
    # thousands of lines).
    with files[-1].open(encoding="utf-8") as f:
        last = deque(f, maxlen=count)
    for line in last:
        line = line.rstrip("\n")
        if line:
            print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-runner",
        description="Restart-on-exit supervisor for autonomous CLI agents.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("./agent-runner.toml"),
        help="Path to agent-runner.toml (default: ./agent-runner.toml)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print last round status and exit",
    )
    parser.add_argument(
        "--metrics",
        nargs="?",
        const=20,
        type=int,
        default=None,
        metavar="N",
        help="Print last N lines of metrics (default 20)",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["round"],
        help="Run one round",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    if args.status:
        return _print_status(cfg.runtime.log_dir)
    if args.metrics is not None:
        return _print_metrics(cfg.runtime.log_dir, args.metrics)
    if args.command == "round":
        run_one_round(cfg)
        # Always exit 0; events.jsonl carries the per-round detail.
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
