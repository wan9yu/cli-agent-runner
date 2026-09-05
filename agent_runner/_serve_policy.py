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

# Exit code for the mem-loop give-up cap (0.2.15 coma-preventer, Task 4): serve
# gives up after MEM_LOOP_THRESHOLD consecutive mid-round memory-terminated
# rounds rather than retrying forever. Distinct from CRASH_LOOP_EXIT (75) on
# purpose: this is a "break-then-restart", NOT a deliberate stop — the host
# memory condition may have cleared by the time systemd respawns a fresh serve
# process, so unlike config_broken/crash_loop this code is deliberately absent
# from the unit's RestartPreventExitStatus. 71 = EX_OSERR (sysexits).
MEM_LOOP_EXIT = 71
# Consecutive mem-terminated rounds before serve gives up (break-then-restart).
MEM_LOOP_THRESHOLD = 5

# Exit code for mem_loop's cross-restart escalation (0.2.16 Task 5 — field
# confirmed: NRestarts climbs, never converges). MEM_LOOP_EXIT (71) alone
# resets on every process restart, so a host stuck in sustained pressure
# respawns into the identical break-then-restart loop forever. Once mem_loop
# itself keeps recurring across restarts (see _MEM_LOOP_PERSIST_THRESHOLD),
# this is a DELIBERATE stop instead — "gave up for real, needs a human" — so,
# unlike 71, it IS listed in the unit's RestartPreventExitStatus. 70 =
# EX_SOFTWARE (sysexits); verified free (grep -rnE "= 70\b|code=70\b").
MEM_LOOP_PERSISTENT_EXIT = 70

# Escalation window/threshold for the cross-restart convergence above: prior
# mem_loop events are counted only within the last _MEM_LOOP_PERSIST_WINDOW_S
# seconds (2h) of "now" — old episodes age out on their own, so a host that
# stays healthy for a sustained window earns a fresh restartable 71 again
# instead of ratcheting toward the persistent stop forever (no state file:
# purely events-tail derived, restart-safe by construction). Once this run's
# mem_loop would be the _MEM_LOOP_PERSIST_THRESHOLD-th (3rd) occurrence inside
# the window (~2 prior + this one, ⇒ ~15 mem-terminated kills total), serve
# escalates to MEM_LOOP_PERSISTENT_EXIT instead of the usual 71.
_MEM_LOOP_PERSIST_WINDOW_S = 7200
_MEM_LOOP_PERSIST_THRESHOLD = 3


# Exit-0 no-progress breaker (0.2.16 Task 6). Some CLIs (pi -- see
# builtin_plugins/pi.py's "pi exits 0 on provider failure") exit 0 on a
# provider failure that never reaches the model: `_round_ok = exit_code == 0`
# (api_types.py) reads that as a clean round, so without this breaker an
# invalid credential (or an exhausted-retries outage) spins as a fast,
# invisible "success" loop with no breaker, no back-off, and no alert -- on a
# constrained host, that tight loop is itself a memory-pressure generator.
# "No progress" = exit 0, a SHORT round (< _NO_PROGRESS_SHORT_S), and no
# agent_usage_recorded for it (see _throttle.round_had_no_progress for the
# events-derived scoping). Deliberately reuses CRASH_LOOP_THRESHOLD /
# CRASH_LOOP_EXIT rather than minting new ones: this is the SAME give-up
# verdict as the crash-loop breaker ("an unknown failure kept recurring, stop
# for real"), reached via a different signal (no usage instead of a non-zero
# exit) -- not a new failure class needing its own systemd
# RestartPreventExitStatus entry.
_NO_PROGRESS_SHORT_S = 30


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


def _consecutive_streak_decision(
    *, triggered: bool, consecutive: int, threshold: int, verdict: str
) -> tuple[str, int]:
    """The generic give-up-cap shape both :func:`_mem_loop_decision` and
    :func:`_no_progress_decision` delegate to (0.2.17 Task 2 — de-duplicated
    out of the two, which were byte-identical apart from their reset
    condition, threshold constant, and verdict string). A NEW, SEPARATE,
    private counter from ``post_round_decision``'s crash-loop breaker, not a
    widening of its 3-tuple contract. Returns ``(verdict, consecutive)`` once
    ``consecutive`` reaches ``threshold``; a non-``triggered`` round resets the
    counter to 0."""
    if not triggered:
        return ("continue", 0)
    consecutive += 1
    if consecutive >= threshold:
        return (verdict, consecutive)
    return ("continue", consecutive)


def _mem_loop_decision(
    *, mem_terminated: bool, consecutive: int
) -> tuple[Literal["mem_loop", "continue"], int]:
    """Mem-loop give-up cap (0.2.15 Task 4) — a NEW, SEPARATE, private counter
    from ``post_round_decision``'s crash-loop breaker, not a widening of its
    3-tuple contract. A mem-terminated round already sets ``throttle_active``
    for ``post_round_decision`` (never counted as a crash), so without this
    cap a sustained memory-pressure host would retry forever instead of
    eventually giving up. Returns ``("mem_loop", consecutive)`` once
    ``consecutive`` reaches ``MEM_LOOP_THRESHOLD``; a non-mem-terminated round
    resets the counter to 0, same reset shape as the crash-loop breaker."""
    return _consecutive_streak_decision(
        triggered=mem_terminated,
        consecutive=consecutive,
        threshold=MEM_LOOP_THRESHOLD,
        verdict="mem_loop",
    )


def _no_progress_decision(
    *, no_progress: bool, consecutive: int
) -> tuple[Literal["stalled_no_progress", "continue"], int]:
    """Exit-0 no-progress give-up cap (0.2.16 Task 6) -- a NEW, SEPARATE,
    private counter from ``post_round_decision``'s crash-loop breaker (that one
    keys on ``returncode != 0``; here returncode IS 0, so a round that never
    reached the model would otherwise sail through with no breaker at all).
    Same shape as :func:`_mem_loop_decision`: returns ``("stalled_no_progress",
    consecutive)`` once ``consecutive`` reaches ``CRASH_LOOP_THRESHOLD`` --
    reusing that threshold (and, at the call site, ``CRASH_LOOP_EXIT``) rather
    than minting new ones, since this is the identical give-up verdict reached
    via a different signal. A round WITH progress (usage recorded, non-zero
    exit, or slow) resets the counter to 0, same reset shape as
    crash-loop/mem-loop."""
    return _consecutive_streak_decision(
        triggered=no_progress,
        consecutive=consecutive,
        threshold=CRASH_LOOP_THRESHOLD,
        verdict="stalled_no_progress",
    )


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


# ---------------------------------------------------------------------------
# Round-timeout budget (Group C, seam 3 — 0.2.13). TimeoutStopSec
# (service_unit.py, systemd's SIGKILL deadline after `systemctl stop`) and the
# outer round-wall ceiling (api.outer_round_ceiling_s, the in-process watchdog
# around the round subprocess) were derived independently and could drift —
# TimeoutStopSec only added a flat 60s to the inner round timeout, with no
# margin for the outer ceiling's own reap/git-commit/hook slack, let alone the
# SIGTERM-to-round-child grace on top of that. One function computes both so
# they cannot disagree.
#
# service_unit.py must not import api.py (cycle: api.py already imports
# service_unit.render_serve_unit), so this lives here — a dependency-free leaf
# both service_unit.py and api.py can import. Its margin constants are
# LITERAL mirrors of the real sources of truth (agent_runtime.REAP_GRACE_S,
# vcs_state.GIT_COMMIT_TIMEOUT_S, api._ROUND_TERM_GRACE_S) rather than
# imports, same as service_unit.py's literal "78 75" RestartPreventExitStatus
# mirroring PERMANENT_CONFIG_EXIT/CRASH_LOOP_EXIT — pinned by
# test_timeout_budget_invariant.py so a change to any of the three can't
# silently drift this budget out of sync.
_REAP_GRACE_S = 5
_GIT_COMMIT_TIMEOUT_S = 120
_HOOK_ALLOWANCE_S = 60  # slack for post-round hooks between the inner wall and the ceiling
_ROUND_TERM_GRACE_S = 15  # mirrors api._ROUND_TERM_GRACE_S / cli._serve_round._ROUND_TERM_GRACE_S
_STOP_GRACE_MARGIN_S = 10  # pad above _ROUND_TERM_GRACE_S for systemd stop-request overhead


def timeout_budget(round_timeout_s: int) -> tuple[int, int]:
    """Single source for the round-timeout safety budget.

    Returns ``(timeout_stop_sec, outer_ceiling_s)``:

    - ``outer_ceiling_s`` — the in-process outer wall-clock ceiling for the
      round subprocess (``api.outer_round_ceiling_s``): ``round_timeout_s``
      plus reap grace + git-commit ceiling + hook allowance, so it only trips
      when the round supervisor itself is wedged, never during its own
      bounded post-round cleanup.
    - ``timeout_stop_sec`` — systemd's ``TimeoutStopSec``
      (``service_unit.py``): must clear ``outer_ceiling_s`` by enough for a
      SIGTERM to reach and drain the round (``_ROUND_TERM_GRACE_S``) plus a
      stop-request overhead pad, so `systemctl stop` never SIGKILLs a round
      that is draining normally.
    """
    outer_ceiling_s = round_timeout_s + _REAP_GRACE_S + _GIT_COMMIT_TIMEOUT_S + _HOOK_ALLOWANCE_S
    timeout_stop_sec = outer_ceiling_s + _ROUND_TERM_GRACE_S + _STOP_GRACE_MARGIN_S
    return timeout_stop_sec, outer_ceiling_s
