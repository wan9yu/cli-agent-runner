"""round subcommand — runs one supervisor round (used by serve and systemd)."""

from __future__ import annotations

import signal
import sys
import traceback

from agent_runner._serve_policy import classify_round_exit
from agent_runner.cli.common import cfg_from_args_or_config_error
from agent_runner.config import ConfigError
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
    """Run one round. Exit code is classify_round_exit's permanence verdict
    (Group A): 78 permanent-config, 76 environmental (serve retries), 130
    SIGTERM/SIGINT, 1 for a real agent crash or any OTHER exception — including
    an unclassified supervisor bug, so the crash-loop breaker still bounds it.
    Never returns the serve-reserved 75 (that verdict is serve's own, from
    post_round_decision) — sys.exit(78/76) from run_one_round's own startup
    battery propagates through untouched (SystemExit is not an Exception)."""
    _install_term_handler()
    try:
        cfg = cfg_from_args_or_config_error(args)
    except ConfigError as exc:
        # Same friendly, actionable message serve's boot-time ConfigError gets
        # via main()'s handler -- not a raw traceback. stderr is captured into
        # round-<N>.log, so the operator sees the remedy right there. (A
        # DIFFERENT ConfigError -- _phase_for's stale-serve-cache one, raised
        # below from run_one_round -- carries its own "restart serve" remedy
        # and deliberately does NOT go through this migrate-hint branch.)
        print(
            f"agent-runner: config error: {exc}\nRun `agent-runner migrate` then retry.",
            file=sys.stderr,
        )
        return classify_round_exit(exc)
    try:
        result = run_one_round(cfg, phase_override=args.phase)
    except KeyboardInterrupt as exc:
        # SIGTERM/SIGINT: run_one_round's inner run() already reaped the agent
        # pgroup and released the round lock/sidecar on the way out. No
        # traceback -- this is an expected shutdown, not a crash.
        return classify_round_exit(exc)
    except Exception as exc:
        # classify_round_exit is now the single whitelist (was: only
        # KeyboardInterrupt here; everything else fell through to Python's own
        # uncaught-exception traceback + exit 1). Still print the traceback for
        # anything else -- including _phase_for's ConfigError, whose own
        # "restart serve" remedy text belongs IN the traceback, not overwritten
        # by the generic migrate hint above -- serve captures this subprocess's
        # stderr into round-<N>.log, and it's no less worth diagnosing than a
        # bare 1.
        traceback.print_exc()
        return classify_round_exit(exc)
    # Surface a real agent crash as a non-zero exit so serve's crash-loop breaker
    # (which keys on this subprocess's returncode) can count it. A grace-kill (agent
    # produced a result then lingered) or a timeout (long, not a short crash) is NOT
    # a crash → 0.
    crashed = result.exit_code != 0 and not result.killed_for_grace and not result.timed_out
    return 1 if crashed else 0
