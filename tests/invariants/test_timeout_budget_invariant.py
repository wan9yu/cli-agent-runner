"""Invariant: TimeoutStopSec (systemd's SIGKILL deadline after `systemctl
stop`) must always clear the in-process outer round-wall ceiling
(api.outer_round_ceiling_s) by enough margin for a SIGTERM to reach and drain
the round -- mirrors the _lifecycle._ROUND_TERM_GRACE_S / _serve_round._ROUND_TERM_GRACE_S
grace-pair guard (test_round_kill_grace_should_match_serve_cmd_grace).

_serve_policy.timeout_budget is the single source for both numbers (Group C,
seam 3): service_unit.py's TimeoutStopSec render and api.py's
outer_round_ceiling_s derivation both call it, so they cannot drift apart.
"""

from __future__ import annotations

from agent_runner import _serve_policy
from agent_runner._lifecycle import _ROUND_TERM_GRACE_S
from agent_runner.agent_runtime import REAP_GRACE_S
from agent_runner.cli._serve_cgroup import _ROUND_UNREAPED_RC as _SERVE_CGROUP_UNREAPED_RC
from agent_runner.cli._serve_round import _ROUND_TERM_GRACE_S as _SERVE_ROUND_TERM_GRACE_S
from agent_runner.cli._serve_round import _ROUND_UNREAPED_RC as _SERVE_ROUND_UNREAPED_RC
from agent_runner.config.models import _MAX_SIGTERM_GRACE_S
from agent_runner.vcs_state import GIT_COMMIT_TIMEOUT_S


def test_timeout_stop_sec_should_exceed_outer_ceiling_when_invoked():

    timeout_stop_sec, outer_ceiling_s = _serve_policy.timeout_budget(100)

    actual = timeout_stop_sec

    assert actual > outer_ceiling_s


def test_timeout_stop_sec_should_clear_outer_ceiling_by_at_least_round_term_grace_when_invoked():
    """TimeoutStopSec must not fire before serve's own SIGTERM-to-round-child
    grace has had a chance to work -- otherwise systemd SIGKILLs a round that
    is draining normally."""
    timeout_stop_sec, outer_ceiling_s = _serve_policy.timeout_budget(100)

    actual = timeout_stop_sec - outer_ceiling_s

    assert actual >= _ROUND_TERM_GRACE_S


def test_outer_ceiling_should_bound_worst_case_coop_grace_not_just_default_when_invoked():
    """A cooperative agent's sigterm_grace_s is boot-capped strictly below
    _ROUND_TERM_GRACE_S (config/models.py's _MAX_SIGTERM_GRACE_S mirror-with-
    margin), so the outer ceiling's reap margin must be sized to that worst
    case, not the old fixed REAP_GRACE_S=5 -- else a long cooperative grace
    could trip the outer wall-clock ceiling mid-grace. Sizing off
    _ROUND_TERM_GRACE_S itself (rather than the smaller cap) keeps this bound
    correct even though the cap sits below it."""
    _, outer_ceiling_s = _serve_policy.timeout_budget(100)

    expected = (
        100
        + _serve_policy._ROUND_TERM_GRACE_S
        + _serve_policy._GIT_COMMIT_TIMEOUT_S
        + _serve_policy._HOOK_ALLOWANCE_S
    )

    assert outer_ceiling_s == expected


def test_budget_should_scale_linearly_with_round_timeout_when_invoked():
    a_stop, a_ceiling = _serve_policy.timeout_budget(100)

    b_stop, b_ceiling = _serve_policy.timeout_budget(200)

    assert b_stop - a_stop == 100

    assert b_ceiling - a_ceiling == 100


def test_outer_ceiling_should_grow_by_exactly_the_goal_checks_allowance_when_invoked():
    """The goal-check executor's own time budget folds into the SAME single
    ceiling rather than a second one -- a slow check must never trip
    round_supervisor_wedged (the goal path causing a kill would be a firewall
    breach by timing). ``timeout_stop_sec`` must inherit the SAME growth
    exactly once too -- a stray second ``+ allowance`` applied directly to
    ``timeout_stop_sec`` (double-counting on top of the widened ceiling it's
    already derived from) would make its delta 2N instead of N."""
    stop_without, without = _serve_policy.timeout_budget(100)

    stop_with, with_allowance = _serve_policy.timeout_budget(100, goal_checks_allowance_s=33)

    assert with_allowance - without == 33

    assert stop_with - stop_without == 33


def test_timeout_stop_sec_should_clear_outer_ceiling_with_goal_allowance_when_invoked():
    """service_unit.py's TimeoutStopSec margin (>= _ROUND_TERM_GRACE_S above
    the outer ceiling) must survive a widened ceiling too -- a `systemctl
    stop` must not SIGKILL a round that is draining normally just because a
    goal-check allowance was folded in."""
    timeout_stop_sec, outer_ceiling_s = _serve_policy.timeout_budget(
        100, goal_checks_allowance_s=33
    )

    actual = timeout_stop_sec - outer_ceiling_s

    assert actual >= _ROUND_TERM_GRACE_S


def test_leaf_margin_constants_should_mirror_their_source_of_truth_when_invoked():
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
    same way, not a vacuous self-compare.

    _MAX_SIGTERM_GRACE_S (0.3.5) is NOT an equality mirror: it is boot-capped
    STRICTLY BELOW _serve_policy._ROUND_TERM_GRACE_S, by at least 3s of
    leader-exit margin, so the round leader's own killpg(SIGKILL) of its agent
    always has room to fire before the supervisor's SIGKILL of the leader on
    the out-of-process `agent-runner kill` path (_lifecycle._terminate_round_pid
    TERM-first, then SIGKILL + stray-reap after this same grace)."""
    assert _serve_policy._REAP_GRACE_S == REAP_GRACE_S
    assert _serve_policy._GIT_COMMIT_TIMEOUT_S == GIT_COMMIT_TIMEOUT_S
    assert _ROUND_TERM_GRACE_S == _SERVE_ROUND_TERM_GRACE_S == _serve_policy._ROUND_TERM_GRACE_S
    assert _SERVE_ROUND_UNREAPED_RC == _SERVE_CGROUP_UNREAPED_RC == _serve_policy._ROUND_UNREAPED_RC

    # _MAX_SIGTERM_GRACE_S is a mirror-WITH-MARGIN, not an equality mirror.
    assert _MAX_SIGTERM_GRACE_S < _serve_policy._ROUND_TERM_GRACE_S
    assert _serve_policy._ROUND_TERM_GRACE_S - _MAX_SIGTERM_GRACE_S >= 3
