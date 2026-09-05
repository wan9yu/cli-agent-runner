"""Pure mid-round mem-pressure action decision (0.2.17 Task 2) -- see
``_serve_round._spawn_round``'s hot loop for the dispatcher this feeds.
Subprocess-free by construction, so this is tested directly rather than
through the real-subprocess integration coverage in
tests/integration/test_spawn_round_mem_floor.py."""

from __future__ import annotations

from agent_runner.cli._serve_round import _mid_round_action
from agent_runner.config import MonitorHostHealthConfig


def test_mid_round_action_2x2_at_threshold() -> None:
    on = MonitorHostHealthConfig(in_round_mem_terminate=True)
    off = MonitorHostHealthConfig(in_round_mem_terminate=False)
    streak = on.mem_critical_consecutive_samples  # at threshold

    assert _mid_round_action(on, False, streak) == "terminate"
    assert _mid_round_action(on, True, streak) == "defer"
    # THE load-bearing cell: in_round_mem_terminate=False must win even when
    # defer_to_cgroup=True -- the off switch means no action at all (no
    # terminate, no mem_pressure_deferred_to_cgroup emit), matching current
    # _serve_round.py behavior (the defer branch is nested INSIDE the
    # in_round_mem_terminate guard, never reachable when it's False).
    assert _mid_round_action(off, True, streak) == "count_only"
    assert _mid_round_action(off, False, streak) == "count_only"


def test_mid_round_action_below_threshold_is_always_count_only() -> None:
    cfg = MonitorHostHealthConfig(in_round_mem_terminate=True)
    below = cfg.mem_critical_consecutive_samples - 1

    assert _mid_round_action(cfg, False, below) == "count_only"
    assert _mid_round_action(cfg, True, below) == "count_only"
