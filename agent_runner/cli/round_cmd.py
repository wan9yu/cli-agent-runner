"""round subcommand — runs one supervisor round (used by serve and systemd)."""

from __future__ import annotations

import signal

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


def _install_term_handler() -> None:
    """Convert SIGTERM into KeyboardInterrupt so agent_runtime.run's BaseException
    reap path fires and the agent pgroup is killed before the round exits. SIGINT
    already raises KeyboardInterrupt by default; SIGTERM (serve's stop signal) does
    not, so we install it explicitly."""

    def _raise_term(_sig, _frame):
        raise KeyboardInterrupt("round received SIGTERM")

    signal.signal(signal.SIGTERM, _raise_term)


def cmd(args) -> int:
    """Run one round. 1 on a real agent crash, 130 on SIGTERM/SIGINT, else 0.
    Never returns the serve-reserved 78 / 75 (keep this guarantee from the old
    inline comment — serve's crash-loop breaker keys on this returncode)."""
    _install_term_handler()
    cfg = cfg_from_args(args)
    try:
        result = run_one_round(cfg, phase_override=args.phase)
    except KeyboardInterrupt:
        # SIGTERM/SIGINT: run_one_round's inner run() already reaped the agent
        # pgroup and released the round lock/sidecar on the way out. 130 = shell's
        # SIGINT convention; serve counts it via the normal returncode path.
        return 130
    # Surface a real agent crash as a non-zero exit so serve's crash-loop breaker
    # (which keys on this subprocess's returncode) can count it. A grace-kill (agent
    # produced a result then lingered) or a timeout (long, not a short crash) is NOT
    # a crash → 0.
    crashed = result.exit_code != 0 and not result.killed_for_grace and not result.timed_out
    return 1 if crashed else 0
