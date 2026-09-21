"""v0.3.7 invariants: the soft-brake ships default-OFF, floored, capped, and
its write helpers only ever address serve's OWN leaf (never an ancestor), and
the nudge never escalates to killpg."""

from __future__ import annotations

import inspect

from agent_runner import metrics
from agent_runner.cli import _serve_round
from agent_runner.config import MonitorHostHealthConfig


def test_brake_and_nudge_should_default_off_when_invoked() -> None:
    hh = MonitorHostHealthConfig()

    assert hh.brake.memory_high is False
    assert hh.pressure.in_round_nudge is False


def test_brake_floor_and_engage_guard_should_be_pinned_when_invoked() -> None:
    assert metrics._MIN_MEMORY_HIGH_BYTES == 64 * 1024 * 1024
    assert metrics._MIN_BRAKE_CURRENT_BYTES == 128 * 1024 * 1024


def test_brake_step_pct_should_be_boot_capped_at_50_when_invoked() -> None:
    from agent_runner.config.parsers import _MAX_BRAKE_STEP_PCT

    assert _MAX_BRAKE_STEP_PCT == 50


def test_write_helpers_should_only_target_the_resolved_leaf_never_an_ancestor_when_invoked() -> (
    None
):
    for fn in (metrics.engage_leaf_memory_high, metrics.restore_leaf_memory_high):
        src = inspect.getsource(fn)
        assert "_leaf_dir(" in src  # write target resolved via the leaf helper only
        assert "_bounding_ancestor_path" not in src
        assert "cgroup_memory_high" not in src  # NEVER the ancestor-min read for the stash


def test_nudge_path_should_never_call_killpg_when_invoked() -> None:
    src = inspect.getsource(_serve_round._spawn_round)

    nudge_block = src.split('== "nudge"')[1].split("elif action")[0]
    assert "killpg(" not in nudge_block  # a call, not the block's own "NO killpg" comment
    assert "_terminate_round" not in nudge_block
    assert "proc.terminate()" in nudge_block
