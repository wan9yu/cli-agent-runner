"""Invariant: TimeoutStopSec (systemd's SIGKILL deadline after `systemctl
stop`) must always clear the in-process outer round-wall ceiling
(api.outer_round_ceiling_s) by enough margin for a SIGTERM to reach and drain
the round -- mirrors the api._ROUND_TERM_GRACE_S / _serve_round._ROUND_TERM_GRACE_S
grace-pair guard (test_round_kill_grace_should_match_serve_cmd_grace).

_serve_policy.timeout_budget is the single source for both numbers (Group C,
seam 3): service_unit.py's TimeoutStopSec render and api.py's
outer_round_ceiling_s derivation both call it, so they cannot drift apart.
"""

from __future__ import annotations

from agent_runner import _serve_policy
from agent_runner.agent_runtime import REAP_GRACE_S
from agent_runner.api import _ROUND_TERM_GRACE_S
from agent_runner.cli._serve_cgroup import _ROUND_UNREAPED_RC as _SERVE_CGROUP_UNREAPED_RC
from agent_runner.cli._serve_round import _ROUND_TERM_GRACE_S as _SERVE_ROUND_TERM_GRACE_S
from agent_runner.cli._serve_round import _ROUND_UNREAPED_RC as _SERVE_ROUND_UNREAPED_RC
from agent_runner.vcs_state import GIT_COMMIT_TIMEOUT_S


def test_timeout_stop_sec_should_exceed_outer_ceiling():
    timeout_stop_sec, outer_ceiling_s = _serve_policy.timeout_budget(100)

    assert timeout_stop_sec > outer_ceiling_s


def test_timeout_stop_sec_should_clear_outer_ceiling_by_at_least_round_term_grace():
    """TimeoutStopSec must not fire before serve's own SIGTERM-to-round-child
    grace has had a chance to work -- otherwise systemd SIGKILLs a round that
    is draining normally."""
    timeout_stop_sec, outer_ceiling_s = _serve_policy.timeout_budget(100)

    assert timeout_stop_sec - outer_ceiling_s >= _ROUND_TERM_GRACE_S


def test_budget_should_scale_linearly_with_round_timeout():
    a_stop, a_ceiling = _serve_policy.timeout_budget(100)
    b_stop, b_ceiling = _serve_policy.timeout_budget(200)

    assert b_stop - a_stop == 100
    assert b_ceiling - a_ceiling == 100


def test_leaf_margin_constants_should_mirror_their_source_of_truth():
    """_serve_policy is a dependency-free leaf (service_unit.py must not import
    api.py -- cycle), so two of its margin constants (_REAP_GRACE_S,
    _GIT_COMMIT_TIMEOUT_S) are LITERAL mirrors of the real sources of truth
    (agent_runtime.REAP_GRACE_S, vcs_state.GIT_COMMIT_TIMEOUT_S) rather than
    imports. Pin the mirror here so either hand-authored copy can't silently
    drift the budget out of sync.

    _ROUND_TERM_GRACE_S itself is single-sourced in _serve_policy (0.2.18):
    api.py and cli/_serve_round.py both import it directly rather than
    mirroring a literal, so comparing either one back to _serve_policy alone
    is a vacuous ``x == x`` (same bound object). Instead pin that BOTH real
    importers still see the identical single-sourced value -- guarding the
    "single source, two importers" property itself against a future
    regression (e.g. one side re-acquiring its own literal).

    _ROUND_UNREAPED_RC (0.2.19) is the same shape as _ROUND_TERM_GRACE_S:
    hoisted out of cli/_serve_round.py into this leaf so its sibling module
    cli/_serve_cgroup.py (carved out in the same release) can import it too
    without cycling back through _serve_round. Pin both real importers the
    same way, not a vacuous self-compare."""
    assert _serve_policy._REAP_GRACE_S == REAP_GRACE_S
    assert _serve_policy._GIT_COMMIT_TIMEOUT_S == GIT_COMMIT_TIMEOUT_S
    assert _ROUND_TERM_GRACE_S == _SERVE_ROUND_TERM_GRACE_S == _serve_policy._ROUND_TERM_GRACE_S
    assert _SERVE_ROUND_UNREAPED_RC == _SERVE_CGROUP_UNREAPED_RC == _serve_policy._ROUND_UNREAPED_RC
