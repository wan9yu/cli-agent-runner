"""Pure mid-round mem-pressure action decision (0.2.17 Task 2) -- see
``_serve_round._spawn_round``'s hot loop for the dispatcher this feeds.
Subprocess-free by construction, so this is tested directly rather than
through the real-subprocess integration coverage in
tests/integration/test_spawn_round_mem_floor.py."""

from __future__ import annotations

from agent_runner.cli._serve_round import _mid_round_action
from agent_runner.config import MonitorHostHealthConfig, _HostHealthPressureConfig


def test_mid_round_action_should_match_2x2_matrix_at_threshold_when_invoked() -> None:
    on = MonitorHostHealthConfig(pressure=_HostHealthPressureConfig(in_round_terminate=True))

    off = MonitorHostHealthConfig(pressure=_HostHealthPressureConfig(in_round_terminate=False))
    streak = on.pressure.critical_consecutive_samples  # at threshold

    assert _mid_round_action(on, False, streak, nudged=False) == "terminate"
    assert _mid_round_action(on, True, streak, nudged=False) == "defer"
    # THE load-bearing cell: in_round_terminate=False must win even when
    # defer_to_cgroup=True -- the off switch means no action at all (no
    # terminate, no mem_pressure_deferred_to_cgroup emit), matching current
    # _serve_round.py behavior (the defer branch is nested INSIDE the
    # in_round_terminate guard, never reachable when it's False).
    assert _mid_round_action(off, True, streak, nudged=False) == "count_only"

    assert _mid_round_action(off, False, streak, nudged=False) == "count_only"


def test_mid_round_action_should_be_count_only_when_below_threshold() -> None:
    cfg = MonitorHostHealthConfig(pressure=_HostHealthPressureConfig(in_round_terminate=True))

    below = cfg.pressure.critical_consecutive_samples - 1

    assert _mid_round_action(cfg, False, below, nudged=False) == "count_only"

    assert _mid_round_action(cfg, True, below, nudged=False) == "count_only"


def _cfg(**pressure):
    return MonitorHostHealthConfig(pressure=_HostHealthPressureConfig(**pressure))


def test_mid_round_action_should_nudge_at_streak_one_when_nudge_on_and_hard_would_terminate() -> (
    None
):
    cfg = _cfg(in_round_nudge=True)

    actual = _mid_round_action(cfg, defer_to_cgroup=False, critical_streak=1, nudged=False)

    assert actual == "nudge"


def test_mid_round_action_should_not_nudge_again_once_nudged_when_invoked() -> None:
    cfg = _cfg(in_round_nudge=True)

    actual = _mid_round_action(cfg, defer_to_cgroup=False, critical_streak=1, nudged=True)

    assert actual == "count_only"


def test_mid_round_action_should_not_nudge_when_cgroup_defer_disables_the_hard_verdict() -> None:
    cfg = _cfg(in_round_nudge=True)

    actual = _mid_round_action(cfg, defer_to_cgroup=True, critical_streak=1, nudged=False)

    assert actual == "count_only"


def test_mid_round_action_should_still_terminate_at_the_hard_threshold_when_invoked() -> None:
    cfg = _cfg(in_round_nudge=True)

    actual = _mid_round_action(cfg, defer_to_cgroup=False, critical_streak=3, nudged=True)

    assert actual == "terminate"


def test_mid_round_action_should_not_nudge_when_nudge_off() -> None:
    cfg = _cfg(in_round_nudge=False)

    actual = _mid_round_action(cfg, defer_to_cgroup=False, critical_streak=1, nudged=False)

    assert actual == "count_only"
