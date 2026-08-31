"""_serve_policy is the canonical home of the serve restart policy; api re-exports it."""

from __future__ import annotations


def test_serve_policy_exports_constants_and_decision() -> None:
    from agent_runner import _serve_policy as sp

    assert sp.PERMANENT_CONFIG_EXIT == 78
    assert sp.CRASH_LOOP_EXIT == 75
    assert sp.ENV_BATTERY_EXIT == 76
    action, _, n = sp.post_round_decision(
        returncode=sp.PERMANENT_CONFIG_EXIT,
        duration_s=1.0,
        throttle_active=False,
        consecutive=0,
        restart_delay_s=5,
    )
    assert action == "config_broken"


def test_api_facade_re_exports_the_same_policy_objects() -> None:
    from agent_runner import _serve_policy as sp
    from agent_runner import api

    assert api.post_round_decision is sp.post_round_decision
    assert api.PERMANENT_CONFIG_EXIT is sp.PERMANENT_CONFIG_EXIT
    assert api.CRASH_LOOP_EXIT is sp.CRASH_LOOP_EXIT
    assert api.ENV_BATTERY_EXIT is sp.ENV_BATTERY_EXIT
