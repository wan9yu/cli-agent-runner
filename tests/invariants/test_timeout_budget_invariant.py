"""Invariant: TimeoutStopSec (systemd's SIGKILL deadline after `systemctl
stop`) must always clear the in-process outer round-wall ceiling
(api.outer_round_ceiling_s) by enough margin for a SIGTERM to reach and drain
the round -- mirrors the api._ROUND_TERM_GRACE_S / _serve_round._ROUND_TERM_GRACE_S
grace-pair guard (test_round_kill_grace_matches_serve_cmd_grace).

_serve_policy.timeout_budget is the single source for both numbers (Group C,
seam 3): service_unit.py's TimeoutStopSec render and api.py's
outer_round_ceiling_s derivation both call it, so they cannot drift apart.
"""

from __future__ import annotations

from agent_runner import _serve_policy
from agent_runner.agent_runtime import REAP_GRACE_S
from agent_runner.api import _ROUND_TERM_GRACE_S
from agent_runner.vcs_state import GIT_COMMIT_TIMEOUT_S


def test_timeout_stop_sec_exceeds_outer_ceiling():
    timeout_stop_sec, outer_ceiling_s = _serve_policy.timeout_budget(100)
    assert timeout_stop_sec > outer_ceiling_s


def test_timeout_stop_sec_clears_outer_ceiling_by_at_least_round_term_grace():
    """TimeoutStopSec must not fire before serve's own SIGTERM-to-round-child
    grace has had a chance to work -- otherwise systemd SIGKILLs a round that
    is draining normally."""
    timeout_stop_sec, outer_ceiling_s = _serve_policy.timeout_budget(100)
    assert timeout_stop_sec - outer_ceiling_s >= _ROUND_TERM_GRACE_S


def test_budget_scales_linearly_with_round_timeout():
    a_stop, a_ceiling = _serve_policy.timeout_budget(100)
    b_stop, b_ceiling = _serve_policy.timeout_budget(200)
    assert b_stop - a_stop == 100
    assert b_ceiling - a_ceiling == 100


def test_leaf_margin_constants_mirror_their_source_of_truth():
    """_serve_policy is a dependency-free leaf (service_unit.py must not import
    api.py -- cycle), so its margin constants are LITERAL mirrors of the real
    sources of truth rather than imports. Pin the mirror here so a change to
    any of the three can't silently drift the budget out of sync."""
    assert _serve_policy._REAP_GRACE_S == REAP_GRACE_S
    assert _serve_policy._GIT_COMMIT_TIMEOUT_S == GIT_COMMIT_TIMEOUT_S
    assert _serve_policy._ROUND_TERM_GRACE_S == _ROUND_TERM_GRACE_S
