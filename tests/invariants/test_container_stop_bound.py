"""The best-effort container stop must stay bounded within the round-terminate
grace, or serve's round-terminate escalation would SIGKILL `round_cmd` while a
`<runtime> stop` is still running -- defeating the clean container stop. The
two operands are independently-owned constants in separate modules
(`agent_runtime.REAP_GRACE_S` feeds the stop bound; `_serve_policy` owns the
round grace), and this project retunes such constants release over release, so
the inequality is pinned here instead of surviving only as a code comment."""

from __future__ import annotations

from agent_runner._serve_policy import _ROUND_TERM_GRACE_S
from agent_runner.agent_runtime import _CONTAINER_STOP_TIMEOUT_S


def test_container_stop_timeout_should_stay_within_round_terminate_grace():
    stop_timeout_s = _CONTAINER_STOP_TIMEOUT_S

    assert stop_timeout_s <= _ROUND_TERM_GRACE_S
