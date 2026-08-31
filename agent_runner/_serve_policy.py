"""Pure serve restart policy — no I/O, no clock, no events read.

Extracted from api.py (0.2.12 Group G): serve_cmd imports the decision + exit
codes from here directly (see the architecture allowlist); api.py re-exports
them for external back-compat. Kept dependency-free so the serve loop stays a
thin dispatcher and the policy is unit-testable in isolation.
"""

from __future__ import annotations

from typing import Literal

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
# the unit's RestartPreventExitStatus (stays "78 75") so 76 restarts; a
# *persistent* environmental outage is then bounded by the unit's
# StartLimitBurst window (0.2.12). 76 = EX_NOINPUT (sysexits).
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
    exactly like an active throttle: it escalates the delay but never counts toward
    the crash-loop breaker, so a short environmental outage (ENOSPC, mount hiccup)
    never trips ``crash_loop``. A *persistent* environmental failure keeps serve
    alive at the doubled delay and is ultimately bounded by the systemd unit's
    StartLimit window, not by this breaker.
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
