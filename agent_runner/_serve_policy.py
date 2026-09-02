"""Pure serve restart policy — no I/O, no clock, no events read.

Extracted from api.py (0.2.12 Group G): serve_cmd imports the decision + exit
codes from here directly (see the architecture allowlist); api.py re-exports
them for external back-compat. Kept dependency-free so the serve loop stays a
thin dispatcher and the policy is unit-testable in isolation.
"""

from __future__ import annotations

from typing import Literal

from agent_runner.config import ConfigError

# Exit code for a permanent (no-retry) startup-battery failure. A broken config
# does not self-heal between rounds, so serve STOPS rather than respawning it
# forever. 78 = EX_CONFIG (sysexits) — avoids argparse's 2 and the generic 1.
PERMANENT_CONFIG_EXIT = 78

# Exit code serve returns when the crash-loop breaker trips (a give-up stop, like
# config_broken). Distinct from 78 so `systemctl status` / SuccessExitStatus can
# tell the two "needs intervention" stops apart. 75 = EX_TEMPFAIL (sysexits).
# The systemd unit lists both in RestartPreventExitStatus so a deliberate stop
# stays stopped (and visibly failed) while an unexpected supervisor crash restarts.
CRASH_LOOP_EXIT = 75

# Exit code for an ENVIRONMENTAL startup-battery failure (ENOSPC, mount hiccup,
# an unclassified check): recoverable, unlike a permanent config break. NOT in
# the unit's RestartPreventExitStatus (stays "78 75") so 76 restarts; treated
# exactly like an active throttle (post_round_decision), so serve keeps
# retrying at a fixed doubled delay with the crash-loop breaker disarmed,
# staying alive until the environment heals. StartLimit bounds only repeated
# serve-PROCESS death, not this round-level path. 76 = EX_PROTOCOL (sysexits).
ENV_BATTERY_EXIT = 76

# Crash-loop circuit breaker (b12). The serve loop escalates the restart delay
# on consecutive UNKNOWN short crashes (non-zero exit, short duration, no
# classified transient) and STOPS after CRASH_LOOP_THRESHOLD of them — the Run 6
# ~100-empty-rounds scar. Recoverable-slow failures (rate limit / 5h quota / 5xx
# / timeout) are already handled by the transient-error throttle and never reach
# this path. A clean (exit 0), long, or classified-transient round resets the run.
CRASH_LOOP_THRESHOLD = 5
CRASH_LOOP_SHORT_EXIT_S = 60  # mirrors monitor.SHORT_EXIT_THRESHOLD_S
CRASH_LOOP_MAX_DELAY_S = 1800  # cap the escalating restart delay (30 min)


class EnvironmentalError(Exception):
    """Marks a round-child failure as ENVIRONMENTAL (recoverable) rather than a
    permanent config break — classify_round_exit maps it to ENV_BATTERY_EXIT
    (76), same treatment as an active throttle: serve retries at a flat back-off
    with the crash-loop breaker disarmed.

    A raise site elsewhere in the package (runner.LockHeldError,
    vcs_state.GitTimeout) mixes this in alongside its own base class instead of
    _serve_policy importing every module that raises one — this leaf module
    sits BELOW runner.py/vcs_state.py in the import graph (both reach here via
    api.py already), so importing upward would cycle. classify_round_exit
    matches this marker, not the concrete class or its OTHER base.
    """


# The action strings below are the restart-action enum, not events.py kinds:
# "continue" has no constant, and a constant cannot sit inside Literal[...].
def post_round_decision(
    *,
    returncode: int,
    duration_s: float,
    throttle_active: bool,
    consecutive: int,
    restart_delay_s: int,
) -> tuple[Literal["config_broken", "crash_loop", "continue"], int, int]:
    """Restart policy after one round — keeps the serve loop a thin dispatcher.

    Returns ``(action, delay_s, consecutive)`` where action is:
    - ``"config_broken"`` — permanent startup failure (b18): stop.
    - ``"crash_loop"`` — CRASH_LOOP_THRESHOLD consecutive unknown short crashes
      (b12): stop. An unknown short crash is a non-zero, fast exit with no
      classified transient (rate-limit/5xx/timeout are handled by the throttle).
    - ``"continue"`` — sleep ``delay_s`` then run the next round.

    A clean (exit 0), long, or transient round resets ``consecutive`` to 0; an
    unknown short crash escalates the delay (restart × 2ⁿ, capped) until the stop.
    An ``ENV_BATTERY_EXIT`` (76, environmental startup-battery failure) is treated
    exactly like an active throttle: it takes the flat ``restart_delay_s * 2``
    delay below (never escalating) and resets ``consecutive`` to 0, so it never
    counts toward the crash-loop breaker — a short environmental outage (ENOSPC,
    mount hiccup) never trips ``crash_loop``. This always returns ``"continue"``,
    so serve itself never exits 76 and systemd's StartLimit window never engages
    for this path; a *persistent* environmental failure keeps serve alive
    retrying at the doubled delay until the environment heals. StartLimit bounds
    only repeated serve-PROCESS death, independent of this round-level path.
    """
    if returncode == PERMANENT_CONFIG_EXIT:
        return ("config_broken", 0, consecutive)
    throttle_active = throttle_active or returncode == ENV_BATTERY_EXIT
    unknown_short_crash = (
        returncode != 0 and duration_s < CRASH_LOOP_SHORT_EXIT_S and not throttle_active
    )
    if unknown_short_crash:
        consecutive += 1
        if consecutive >= CRASH_LOOP_THRESHOLD:
            return ("crash_loop", 0, consecutive)
        delay = min(restart_delay_s * 2**consecutive, CRASH_LOOP_MAX_DELAY_S)
        return ("continue", delay, consecutive)
    delay = restart_delay_s if returncode == 0 else restart_delay_s * 2
    return ("continue", delay, 0)


def classify_round_exit(exc: BaseException) -> int:
    """The round-child exit-code CLASSIFIER (Group A) — permanence, not exception
    identity, decides serve's response. Replaces the old exception *whitelist*
    (``cli/__init__.py`` mapped only ``ConfigError``->78, ``round_cmd.py`` only
    ``KeyboardInterrupt``->130; ~10 other classes fell through to Python's own
    uncaught-exception exit 1 — neither retry-76 nor stay-stopped-78/75).

    - ``ConfigError`` (won't self-heal) -> ``PERMANENT_CONFIG_EXIT`` (78).
    - ``EnvironmentalError``-marked (self-heals) -> ``ENV_BATTERY_EXIT`` (76).
    - ``KeyboardInterrupt`` (SIGTERM/SIGINT) -> 130, the shell's convention.
    - ``SystemExit`` passes its own code through unchanged (an int code; a
      non-int/absent code — argparse-style ``sys.exit("msg")`` or a bare
      ``sys.exit()`` — falls to 1, same as unclassified).
    - Anything else, INCLUDING a totally unclassified traceback, falls to 1 so
      the crash-loop breaker still bounds a genuine supervisor bug. Do NOT
      default unclassified -> 76: ``post_round_decision`` resets its counter on
      76, so an arbitrary bug would loop forever at the doubled delay with no
      breaker and no detector. The battery's own "unclassified -> environmental"
      rule (``startup_check.py``) governs *authored probes*, not arbitrary round
      tracebacks — a different trust boundary.

    Never returns ``CRASH_LOOP_EXIT`` (75) — that verdict belongs exclusively to
    serve's own ``post_round_decision``, counting consecutive unknown short
    crashes; a round child never claims it directly.
    """
    if isinstance(exc, SystemExit):
        return exc.code if isinstance(exc.code, int) else 1
    if isinstance(exc, KeyboardInterrupt):
        return 130
    if isinstance(exc, ConfigError):
        return PERMANENT_CONFIG_EXIT
    if isinstance(exc, EnvironmentalError):
        return ENV_BATTERY_EXIT
    return 1
