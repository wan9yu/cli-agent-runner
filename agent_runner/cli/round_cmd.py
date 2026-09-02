"""round subcommand — runs one supervisor round (used by serve and systemd)."""

from __future__ import annotations

import signal
import tomllib
import traceback

from agent_runner._serve_policy import classify_round_exit
from agent_runner.cli.common import cfg_from_args
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
        try:
            cfg = cfg_from_args(args)
        except (FileNotFoundError, tomllib.TOMLDecodeError) as e:
            # config.py's own raise sites stay FileNotFoundError/TOMLDecodeError
            # (other CLI commands distinguish "no config yet" from "bad config");
            # the round/serve path alone needs this typed so classify_round_exit
            # recognizes it as permanent instead of falling to the default 1 —
            # a single bad load is fatal to serve (post_round_decision reads 78
            # as config_broken), not a 5-consecutive-restart crash loop.
            raise ConfigError(str(e)) from e
        result = run_one_round(cfg, phase_override=args.phase)
    except KeyboardInterrupt as exc:
        # SIGTERM/SIGINT: run_one_round's inner run() already reaped the agent
        # pgroup and released the round lock/sidecar on the way out. No
        # traceback -- this is an expected shutdown, not a crash.
        return classify_round_exit(exc)
    except Exception as exc:
        # classify_round_exit is now the single whitelist (was: only
        # KeyboardInterrupt here; everything else fell through to Python's own
        # uncaught-exception traceback + exit 1). Still print the traceback --
        # serve captures this subprocess's stderr into round-<N>.log, and a
        # classified 78/76 verdict is no less worth diagnosing than a bare 1.
        traceback.print_exc()
        return classify_round_exit(exc)
    # Surface a real agent crash as a non-zero exit so serve's crash-loop breaker
    # (which keys on this subprocess's returncode) can count it. A grace-kill (agent
    # produced a result then lingered) or a timeout (long, not a short crash) is NOT
    # a crash → 0.
    crashed = result.exit_code != 0 and not result.killed_for_grace and not result.timed_out
    return 1 if crashed else 0
