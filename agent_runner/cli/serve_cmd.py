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

from agent_runner.api import check_self_terminated_sentinel
from agent_runner.cli.common import cfg_from_args
from agent_runner.hooks import run_serve_startup_hooks
from agent_runner.lifecycle import PIDFile, send_signal_to_pid


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

    # Clean stale sentinel from any previous run so we don't immediately stop.
    (log_dir / ".agent-done").unlink(missing_ok=True)

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
            r = subprocess.run(
                [sys.executable, "-m", "agent_runner.cli", "--config", str(args.config), "round"],
                env=round_env,
            )
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
