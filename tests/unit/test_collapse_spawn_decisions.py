from __future__ import annotations

from agent_runner.api_types import SpawnDecision
from agent_runner.hooks import collapse_spawn_decisions


def test_collapse_should_return_skip_when_any_hook_skips():
    named = [("a", SpawnDecision("proceed")), ("b", SpawnDecision("skip", reason="halt"))]

    assert collapse_spawn_decisions(named).action == "skip"


def test_collapse_should_return_max_defer_when_multiple_defers_and_no_skip():
    named = [("a", SpawnDecision("defer", defer_s=10)), ("b", SpawnDecision("defer", defer_s=30))]

    assert collapse_spawn_decisions(named).defer_s == 30


def test_collapse_should_return_proceed_when_all_proceed():
    named = [("a", SpawnDecision("proceed")), ("b", SpawnDecision("proceed"))]

    assert collapse_spawn_decisions(named).action == "proceed"


def test_collapse_should_break_defer_ties_by_registration_order():
    first = SpawnDecision("defer", defer_s=30, reason="first")
    second = SpawnDecision("defer", defer_s=30, reason="second")

    assert collapse_spawn_decisions([("a", first), ("b", second)]).reason == "first"


def test_collapse_should_return_proceed_when_named_empty():
    assert collapse_spawn_decisions([]).action == "proceed"
