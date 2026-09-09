"""Invariant: the per-round readers the serve loop calls once per round
(`round_outcome`, `_active_throttles`, `post_round_decision`) must not grow
their allocations round over round. Runs a representative read sequence
repeatedly under `tracemalloc` and bounds the steady-state delta between two
later rounds -- a future change that leaks memory per round (an unbounded
cache, a list appended to and never cleared, ...) trips this.

First-call one-time costs (compiled regexes, module-level caches populated
on first use, etc.) are warmed up before the measured rounds so they are not
mistaken for per-round growth."""

from __future__ import annotations

import tracemalloc
from pathlib import Path

from agent_runner import _round_outcome, _throttle
from agent_runner._emit import (
    emit_agent_usage_recorded,
    emit_round_substrate_before,
    emit_transient_error_detected,
    emit_transient_error_recovered,
)
from agent_runner._serve_policy import post_round_decision
from tests._clock import FakeClock

# Steady-state per-round growth ceiling. This is the one measurement-dependent
# gate in the suite -- kept deliberately loose (a real per-round leak grows
# without bound, not by a few hundred bytes) so it stays robust rather than
# tripping on incidental interpreter noise.
_ALLOC_BUDGET_BYTES = 64 * 1024

# Exceeds the first-call cost of every reader's one-time state (regex
# compilation, first-touch of any module-level cache) so the measured rounds
# below only see genuine steady-state, round-over-round cost. Measured across
# repeated fresh-interpreter runs (including the active-throttle seed below,
# which walks _backoff_exponent/_extend_reset): 3 warm-up rounds left the
# 2->3 delta noisy; 6 rounds settles it to a ~880-1130B band (hash-seed
# dict-resize jitter between processes) -- still ~60x under the budget below.
_WARMUP_ROUNDS = 6


def _seed_events(log_dir: Path) -> None:
    """One round's worth of realistic, multi-kind events so `_tail_events`
    (which every reader below folds over) does real work each call -- an
    empty log_dir would make every reader a zero-iteration no-op and the
    gate vacuous.

    Two agents' throttle history, deliberately different shapes:
    - `gemini` recovered (detected, then recovered) -- exercises
      `_active_throttles`'s "latest is a recovery" short-circuit.
    - `codex` is STILL throttled (two detections, no recovery) -- without
      this, every agent in `_active_throttles`'s per-agent loop would hit
      `if detected is None: continue` and the loop would never reach
      `_events_derived_reset` -> `_backoff_exponent` (its own extra
      `_tail_events` scan) -> `_extend_reset`, leaving that sub-path
      unmeasured. Two detections (not one) also give it a nonzero backoff
      exponent so `_extend_reset`'s actual extension arithmetic runs, not
      just its early-return branch."""
    emit_round_substrate_before(log_dir, round_num=1, git_head="a", paths_hash=None)
    emit_agent_usage_recorded(
        log_dir,
        agent="claude",
        model="claude-sonnet",
        round_num=1,
        input_tokens=100,
        output_tokens=50,
        cached_tokens=0,
        cost_usd=0.01,
        duration_ms=1000,
    )
    emit_transient_error_detected(
        log_dir,
        classification="rate_limit",
        agent="gemini",
        reset_at_epoch=1_700_000_300,
        round_num=1,
        raw="rate limited",
    )
    emit_transient_error_recovered(
        log_dir, classification="rate_limit", agent="gemini", throttled_for_s=60
    )
    for round_num in (1, 2):
        emit_transient_error_detected(
            log_dir,
            classification="api_transient_5xx",
            agent="codex",
            reset_at_epoch=1_700_000_010,
            round_num=round_num,
            raw="server error",
        )


def _one_round(log_dir: Path, clock: FakeClock) -> _round_outcome.RoundOutcome:
    outcome = _round_outcome.round_outcome(log_dir)
    _throttle._active_throttles(log_dir, clock=clock)
    post_round_decision(
        returncode=0,
        duration_s=1.0,
        throttle_active=False,
        consecutive=0,
        restart_delay_s=3,
    )
    return outcome


def test_per_round_readers_should_not_grow_allocations_when_run_across_rounds(
    tmp_path: Path,
) -> None:
    clock = FakeClock(epoch=1_700_000_000.0)
    _seed_events(tmp_path)

    for _ in range(_WARMUP_ROUNDS):
        _one_round(tmp_path, clock)

    tracemalloc.start()
    _one_round(tmp_path, clock)
    snap2 = tracemalloc.take_snapshot()
    _one_round(tmp_path, clock)
    snap3 = tracemalloc.take_snapshot()
    tracemalloc.stop()

    grown = sum(
        stat.size_diff for stat in snap3.compare_to(snap2, "filename") if stat.size_diff > 0
    )
    assert grown < _ALLOC_BUDGET_BYTES, (
        f"per-round allocation growth {grown}B exceeds the {_ALLOC_BUDGET_BYTES}B budget "
        "-- a per-round reader may be leaking state round over round"
    )
