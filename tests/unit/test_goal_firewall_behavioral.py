"""P2 firewall BEHAVIORAL differential -- the events-derived complement to the
AST firewall.

The AST firewall proves, statically, that no kill-path module even references a
``goal_*`` kind string. This file proves the runtime PROPERTY that static
check exists to guarantee: :func:`agent_runner._round_outcome.round_outcome`
-- the events-tail fold every kill/give-up verdict
(:func:`round_was_mem_terminated`, :func:`round_had_no_progress`, and
``post_round_verdicts``'s crash-loop/mem-loop/no-progress breakers, all of
which consume a ``RoundOutcome``) is built from -- returns a BYTE-IDENTICAL
``RoundOutcome`` whether or not ``goal_check``/``goal_assessment`` events are
interleaved into the scanned events tail.

Non-vacuity (two independent guards):

1. A raw self-check that the WITH-goal tail's file actually contains the
   goal_* kind strings and the WITHOUT-goal tail's file does not -- so a bug
   that silently dropped ALL sprinkled events before they even reached disk
   couldn't make this test's setup a no-op.
2. The fixture's real (kill-path-relevant) events are non-trivial -- they
   drive every field on ``RoundOutcome`` to a non-default value (mem
   terminated, usage-capable, a live transient backoff). A ``round_outcome``
   that went blind to EVERYTHING (not just goal_*) would make the WITH/WITHOUT
   comparison vacuously pass; asserting the real signal is present rules that
   degenerate case out.

The sprinkled goal_* events are additionally adversarial: a LATER timestamp
than any real event, a distinct sentinel ``agent`` label, and field names that
shadow real kill-path fields (``success``, ``exit_code``) -- so if
``round_outcome`` ever grew a branch that (even by accident) treated a
``goal_check``/``goal_assessment`` kind like ``agent_usage_recorded`` or
``transient_error_detected``, the WITH-goal outcome would visibly diverge
from the WITHOUT-goal one (a later ``newest_usage_ts``, a spurious
``usage_capable_by_agent`` entry for the sentinel agent, etc.) and this test
would fail.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_runner._round_outcome import round_outcome
from agent_runner.events import (
    AGENT_USAGE_RECORDED,
    GOAL_ASSESSMENT,
    GOAL_CHECK,
    ROUND_MEM_TERMINATED,
    ROUND_SUBSTRATE_BEFORE,
    TRANSIENT_ERROR_DETECTED,
)

_MONTH_FILE = "events-2026-01.jsonl"

# The real, kill-path-relevant events round_outcome actually reads (see its
# docstring / source): round_substrate_before, agent_usage_recorded,
# transient_error_detected, round_mem_terminated. Deliberately drives every
# RoundOutcome field to a non-default value (see module docstring, guard 2).
_REAL_EVENTS: list[dict] = [
    {
        "ts": "2026-01-01T00:00:00.000Z",
        "event": ROUND_SUBSTRATE_BEFORE,
        "round_num": 1,
    },
    {
        "ts": "2026-01-01T00:00:01.000Z",
        "event": AGENT_USAGE_RECORDED,
        "agent": "claude",
        "round_num": 1,
        "success": True,
    },
    {
        "ts": "2026-01-01T00:00:02.000Z",
        "event": TRANSIENT_ERROR_DETECTED,
        "agent": "claude",
        "classification": "api_transient_5xx",
        "reset_at_epoch": 99999999999,
        "round_num": 1,
    },
    {
        "ts": "2026-01-01T00:00:03.000Z",
        "event": ROUND_MEM_TERMINATED,
        "round_num": 1,
    },
]

# Adversarial goal_* events: LATER ts than any real event, a sentinel agent
# label distinct from any real agent, and field names that shadow real
# kill-path fields -- see module docstring for why this shape has teeth.
_GOAL_EVENTS: list[dict] = [
    {
        "ts": "2099-01-01T00:00:00.000Z",
        "event": GOAL_CHECK,
        "agent": "sentinel-goal-agent",
        "round_num": 999,
        "name": "done_check",
        "passed": False,
        "success": True,
    },
    {
        "ts": "2099-01-01T00:00:01.000Z",
        "event": GOAL_ASSESSMENT,
        "agent": "sentinel-goal-agent",
        "round_num": 999,
        "verdict": "stuck",
        "confidence": 0.9,
    },
]


def _write(log_dir: Path, *events: dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / _MONTH_FILE
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_round_outcome_should_be_byte_identical_with_and_without_goal_events(
    tmp_path: Path,
) -> None:
    with_dir = tmp_path / "with_goal" / "logs"
    without_dir = tmp_path / "without_goal" / "logs"

    # Interleave the goal_* events among the real ones (position is
    # irrelevant to round_outcome's per-kind branches -- see its source --
    # interleaving just proves that irrelevance rather than assuming it).
    _write(
        with_dir,
        _REAL_EVENTS[0],
        _GOAL_EVENTS[0],
        _REAL_EVENTS[1],
        _GOAL_EVENTS[1],
        _REAL_EVENTS[2],
        _REAL_EVENTS[3],
    )
    _write(without_dir, *_REAL_EVENTS)

    # Guard 1 (non-vacuity): the two tails genuinely differ on disk.
    with_text = (with_dir / _MONTH_FILE).read_text(encoding="utf-8")
    without_text = (without_dir / _MONTH_FILE).read_text(encoding="utf-8")
    assert GOAL_CHECK in with_text and GOAL_ASSESSMENT in with_text, (
        "test setup bug: the WITH-goal tail must actually contain goal_* events"
    )
    assert GOAL_CHECK not in without_text and GOAL_ASSESSMENT not in without_text, (
        "test setup bug: the WITHOUT-goal tail must contain no goal_* events"
    )

    outcome_with = round_outcome(with_dir, ran_agent="claude")
    outcome_without = round_outcome(without_dir, ran_agent="claude")

    # The firewall property: goal_* events in the tail change NOTHING about
    # the events-derived kill-path verdict fold.
    assert outcome_with == outcome_without

    # Guard 2 (non-vacuity): the shared outcome carries real signal -- proves
    # round_outcome read the REAL events correctly, not that it went blind to
    # everything (which would also satisfy the equality above vacuously).
    assert outcome_with.mem_terminated is True
    assert outcome_with.usage_capable is True
    assert outcome_with.newest_usage_ts == "2026-01-01T00:00:01.000Z"
    assert outcome_with.newest_substrate_before_ts == "2026-01-01T00:00:00.000Z"
    assert outcome_with.usage_capable_by_agent == {"claude": True}
    assert outcome_with.ran_agent == "claude"
    assert outcome_with.latest_transient_per_agent["claude"]["classification"] == (
        "api_transient_5xx"
    )
    # The sentinel goal agent must never appear in any per-agent fold --
    # proves round_outcome never even looked at the goal events' agent field.
    assert "sentinel-goal-agent" not in outcome_with.usage_capable_by_agent
    assert "sentinel-goal-agent" not in outcome_with.latest_transient_per_agent
    assert "sentinel-goal-agent" not in outcome_with.newest_usage_ts_by_agent
    assert "sentinel-goal-agent" not in outcome_with.backoff_exponent_by_agent
