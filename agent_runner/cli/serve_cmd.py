"""serve subcommand — long-running supervisor loop. THIN: <=60 LOC.

Trap signals, write/cleanup PID files, run `round` subprocess in a loop.
All real work delegated to `agent-runner round` (fresh import per round).
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: TID251
import sys
import time
from pathlib import Path

from agent_runner.api import check_self_terminated_sentinel, read_round_num
from agent_runner.cli.common import cfg_from_args
from agent_runner.hooks import run_serve_startup_hooks
from agent_runner.lifecycle import PIDFile, send_signal_to_pid

ROUND_CURRENT_LINK = "round-current.log"


def _atomic_relink(link: Path, target: Path) -> None:
    """Atomically replace ``link`` to point at ``target``.

    Uses ``os.symlink`` + ``os.replace`` pattern: create the symlink at a
    temp path, then atomically rename it to the final link name.
    """
    tmp = link.with_suffix(link.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    os.symlink(target.name, tmp)
    os.replace(tmp, link)


def _prune_old_round_logs(log_dir: Path, retention: int) -> None:
    """Keep most-recent ``retention`` round-*.log files by mtime; unlink the rest."""
    logs = sorted(
        log_dir.glob("round-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Exclude the symlink itself (it's not a regular round log file)
    logs = [p for p in logs if p.name != ROUND_CURRENT_LINK]
    for old in logs[retention:]:
        old.unlink(missing_ok=True)


def _next_round_num(log_dir: Path) -> int:
    """Return the next round number, avoiding reuse of any existing log file numbers."""
    status_num = read_round_num(log_dir)
    file_nums = []
    for p in log_dir.glob("round-*.log"):
        if p.name == ROUND_CURRENT_LINK:
            continue
        stem_parts = p.stem.split("-", 1)
        if len(stem_parts) == 2:
            try:
                file_nums.append(int(stem_parts[1]))
            except ValueError:
                pass
    max_file_num = max(file_nums, default=0)
    return max(status_num, max_file_num) + 1


def add_parser(sub, parent) -> None:
    p = sub.add_parser("serve", parents=[parent], help="Long-running supervisor loop")
    p.add_argument("--once", action="store_true", help="Run a single round then exit (debug)")
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    cfg = cfg_from_args(args)
    log_dir = cfg.runtime.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    if not run_serve_startup_hooks(cfg, log_dir):
        return 1

    (log_dir / ".agent-done").unlink(missing_ok=True)
    _prune_old_round_logs(log_dir, cfg.runtime.round_log_retention)

    pid_file = PIDFile(log_dir / "serve.pid")
    pid_file.write(os.getpid())
    stop = {"requested": False}
    round_pid_file = PIDFile(log_dir / "round.pid")

    def graceful(_sig, _frame):
        stop["requested"] = True

    def cancel(_sig, _frame):
        stop["requested"] = True
        rp = round_pid_file.read()
        if rp is not None:
            send_signal_to_pid(-rp, signal.SIGINT)

    signal.signal(signal.SIGTERM, graceful)
    signal.signal(signal.SIGINT, graceful)
    signal.signal(signal.SIGUSR1, cancel)

    round_env = {**os.environ, "AGENT_RUNNER_LOG_DIR": str(log_dir)}

    try:
        while not stop["requested"]:
            if check_self_terminated_sentinel(log_dir):
                break
            round_num = _next_round_num(log_dir)
            round_log_path = log_dir / f"round-{round_num}.log"
            with round_log_path.open("w") as f:
                r = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "agent_runner.cli",
                        "--config",
                        str(args.config),
                        "round",
                    ],
                    env=round_env,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                )
            _atomic_relink(log_dir / ROUND_CURRENT_LINK, round_log_path)
            if args.once or stop["requested"]:
                break
            delay = (
                cfg.runtime.restart_delay_s
                if r.returncode == 0
                else cfg.runtime.restart_delay_s * 2
            )
            time.sleep(delay)
    finally:
        pid_file.unlink()
    return 0
