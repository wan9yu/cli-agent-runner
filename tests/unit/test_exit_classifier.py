"""classify_round_exit is the permanence classifier for a round child's exit code
(Group A): permanence, not exception identity, drives serve's response. Replaces
the old exception *whitelist* (ConfigError->78, KeyboardInterrupt->130, everything
else fell through to Python's own uncaught-exception exit 1)."""

from __future__ import annotations

import pytest

from agent_runner._serve_policy import EnvironmentalError, classify_round_exit
from agent_runner.config import ConfigError


def test_config_error_is_permanent_78():
    assert classify_round_exit(ConfigError("bad")) == 78


def test_environmental_error_is_76():
    assert classify_round_exit(EnvironmentalError("enospc")) == 76


def test_keyboardinterrupt_is_130():
    assert classify_round_exit(KeyboardInterrupt()) == 130


def test_unclassified_traceback_is_1_not_76():
    # A supervisor bug must hit the crash-loop breaker, never loop forever as 76.
    assert classify_round_exit(RuntimeError("plugin import blew up")) == 1


def test_unicode_decode_error_is_not_78():
    # UnicodeDecodeError subclasses ValueError; must NOT be swept to stay-stopped.
    assert classify_round_exit(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")) != 78


def test_systemexit_passes_through():
    assert classify_round_exit(SystemExit(76)) == 76


def test_systemexit_non_int_code_is_1():
    assert classify_round_exit(SystemExit("some message")) == 1


def test_lock_held_error_is_environmental_76():
    # LockHeldError is one of the *named* environmental classes (Group A spec):
    # a concurrent agent-runner holding the round lock self-heals -- retry, don't
    # count it toward the crash-loop breaker.
    from agent_runner.runner import LockHeldError

    assert classify_round_exit(LockHeldError("another agent-runner is running")) == 76


def test_git_timeout_is_environmental_76():
    from agent_runner.vcs_state import GitTimeout

    assert classify_round_exit(GitTimeout("git status exceeded 10s")) == 76


def test_unclassified_short_crash_trips_crash_loop_after_5():
    """Wires classify_round_exit's "else -> 1" verdict into post_round_decision's
    breaker, end to end: an unnamed traceback must still bound a genuine
    supervisor bug at CRASH_LOOP_EXIT (75), not loop forever like 76 would."""
    from agent_runner._serve_policy import post_round_decision

    exit_code = classify_round_exit(RuntimeError("plugin import blew up"))
    assert exit_code == 1

    consecutive = 0
    action = "continue"
    for _ in range(5):
        action, _delay, consecutive = post_round_decision(
            returncode=exit_code,
            duration_s=1.0,
            throttle_active=False,
            consecutive=consecutive,
            restart_delay_s=3,
        )
    assert action == "crash_loop"
    assert consecutive == 5


@pytest.mark.parametrize(
    "exc",
    [ConfigError("x"), EnvironmentalError("x"), KeyboardInterrupt(), RuntimeError("x")],
)
def test_classify_round_exit_never_returns_75(exc):
    # 75 (CRASH_LOOP_EXIT) is exclusively serve's own verdict from
    # post_round_decision -- a round child must never claim it directly.
    assert classify_round_exit(exc) != 75
