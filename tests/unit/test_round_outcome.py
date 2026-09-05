"""0.2.17 Task 1: round_outcome — a single events-tail scan folding the
per-round mem-terminated / no-progress / throttle-active verdicts that used
to be three separate standalone scans (`round_was_mem_terminated`,
`round_had_no_progress`, the throttle check via `_latest_transient_per_agent`).

Behavior-preserving refactor: these tests pin `round_outcome` and the
`round_was_mem_terminated` / `round_had_no_progress` wrappers to the SAME
verdicts whether they scan fresh (`outcome=None`, the default) or are handed
a precomputed `RoundOutcome` (the serve post-round block's one-scan path)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runner import _throttle
from agent_runner._throttle import (
    RoundOutcome,
    round_had_no_progress,
    round_outcome,
    round_was_mem_terminated,
)
from tests._clock import FakeClock


def _write(log_dir: Path, *events: dict) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "events-2026-08.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


# --- (a) direct round_was_mem_terminated unit test (none existed pre-0.2.17) ----


def test_round_was_mem_terminated_true_when_terminated_after_newest_before(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"ts": "2026-01-01T00:00:00.000Z", "event": "round_substrate_before", "round_num": 1},
        {"ts": "2026-01-01T00:00:05.000Z", "event": "round_mem_terminated", "round_num": 1},
    )
    assert round_was_mem_terminated(log_dir) is True


def test_round_was_mem_terminated_false_when_terminated_before_newest_before(
    tmp_path: Path,
) -> None:
    """A stale round_mem_terminated from an EARLIER round, followed by a fresh
    round_substrate_before for the round that just ran clean -- must not be
    misread as THIS round having been mem-terminated."""
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"ts": "2026-01-01T00:00:00.000Z", "event": "round_mem_terminated", "round_num": 1},
        {"ts": "2026-01-01T00:00:05.000Z", "event": "round_substrate_before", "round_num": 2},
    )
    assert round_was_mem_terminated(log_dir) is False


def test_round_was_mem_terminated_false_when_no_terminated_event(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"ts": "2026-01-01T00:00:00.000Z", "event": "round_substrate_before", "round_num": 1},
    )
    assert round_was_mem_terminated(log_dir) is False


def test_round_was_mem_terminated_true_on_exact_ts_tie(tmp_path: Path) -> None:
    """>=, not >: a fast loop (or ms-resolution ties) can legitimately stamp
    both events in the same millisecond -- erring toward "this round's" only
    risks over-excusing, never mistaking a genuine crash for a rescue."""
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"ts": "2026-01-01T00:00:00.000Z", "event": "round_substrate_before", "round_num": 1},
        {"ts": "2026-01-01T00:00:00.000Z", "event": "round_mem_terminated", "round_num": 1},
    )
    assert round_was_mem_terminated(log_dir) is True


# --- (b) round_outcome + wrappers give the same verdicts, fresh or precomputed --


def test_round_outcome_folds_usage_transient_and_substrate_fields(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"ts": "2026-01-01T00:00:00.000Z", "event": "agent_usage_recorded", "round_num": 1},
        {"ts": "2026-01-01T00:00:01.000Z", "event": "round_substrate_before", "round_num": 2},
        {
            "ts": "2026-01-01T00:00:02.000Z",
            "event": "transient_error_detected",
            "agent": "claude",
            "classification": "api_transient_5xx",
            "reset_at_epoch": 99999999999,
            "round_num": 2,
        },
    )
    outcome = round_outcome(log_dir)
    assert isinstance(outcome, RoundOutcome)
    assert outcome.mem_terminated is False
    assert outcome.usage_capable is True
    assert outcome.newest_usage_ts == "2026-01-01T00:00:00.000Z"
    assert outcome.newest_substrate_before_ts == "2026-01-01T00:00:01.000Z"
    assert outcome.latest_transient_per_agent["claude"]["classification"] == "api_transient_5xx"

    # Wrappers given the precomputed outcome must agree with their own from-scratch
    # scan (outcome=None, the default every existing caller/test still exercises).
    assert round_was_mem_terminated(log_dir, outcome=outcome) == round_was_mem_terminated(log_dir)
    fresh_no_progress = round_had_no_progress(log_dir, returncode=0, duration_s=3.0, threshold_s=30)
    reused_no_progress = round_had_no_progress(
        log_dir, returncode=0, duration_s=3.0, threshold_s=30, outcome=outcome
    )
    assert fresh_no_progress == reused_no_progress
    # This round's usage (00:00:00) predates its own round_substrate_before
    # (00:00:01) -- the CLI never reached the model THIS round: no progress.
    assert reused_no_progress is True


def test_round_outcome_mem_terminated_scenario(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"ts": "2026-01-01T00:00:00.000Z", "event": "round_substrate_before", "round_num": 1},
        {"ts": "2026-01-01T00:00:01.000Z", "event": "round_mem_terminated", "round_num": 1},
    )
    outcome = round_outcome(log_dir)
    assert outcome.mem_terminated is True
    assert round_was_mem_terminated(log_dir, outcome=outcome) is True
    assert round_was_mem_terminated(log_dir, outcome=outcome) == round_was_mem_terminated(log_dir)


def test_round_outcome_usage_present_means_progress(tmp_path: Path) -> None:
    """Usage stamped AT-OR-AFTER this round's round_substrate_before: progress
    was made, so round_had_no_progress must read False off the same outcome."""
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"ts": "2026-01-01T00:00:00.000Z", "event": "round_substrate_before", "round_num": 1},
        {"ts": "2026-01-01T00:00:01.000Z", "event": "agent_usage_recorded", "round_num": 1},
    )
    outcome = round_outcome(log_dir)
    assert outcome.usage_capable is True
    assert outcome.newest_usage_ts == "2026-01-01T00:00:01.000Z"
    no_progress = round_had_no_progress(
        log_dir, returncode=0, duration_s=3.0, threshold_s=30, outcome=outcome
    )
    assert no_progress is False
    assert no_progress == round_had_no_progress(
        log_dir, returncode=0, duration_s=3.0, threshold_s=30
    )


def test_round_outcome_invariant_1_usage_capable_unconditional_on_missing_ts(
    tmp_path: Path,
) -> None:
    """INVARIANT 1: usage_capable is set UNCONDITIONALLY on agent_usage_recorded,
    even when that event carries no (or a falsy) ts -- only newest_usage_ts
    requires a truthy ts to update."""
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"event": "agent_usage_recorded", "round_num": 1},  # no ts at all
        {"ts": "2026-01-01T00:00:01.000Z", "event": "round_substrate_before", "round_num": 2},
    )
    outcome = round_outcome(log_dir)
    assert outcome.usage_capable is True
    assert outcome.newest_usage_ts is None  # never updated -- the one usage event had no ts
    # usage_capable but no usage ts at/after this round's start -> no progress.
    assert (
        round_had_no_progress(
            log_dir, returncode=0, duration_s=3.0, threshold_s=30, outcome=outcome
        )
        is True
    )


def test_round_had_no_progress_early_return_ahead_of_outcome_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """INVARIANT 4: the early-return gates (non-zero exit / slow / throttled) must
    short-circuit BEFORE round_outcome is ever computed. An empty log_dir alone
    does NOT prove this -- a hypothetical impl that resolved outcome BEFORE the
    early return would also pass silently (glob finds nothing to fold either
    way) -- so spy on round_outcome directly and assert it is never called."""

    def _boom(log_dir: Path) -> RoundOutcome:
        raise AssertionError("round_outcome must not be called on the early-return path")

    monkeypatch.setattr(_throttle, "round_outcome", _boom)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    assert round_had_no_progress(log_dir, returncode=1, duration_s=3.0, threshold_s=30) is False
    assert round_had_no_progress(log_dir, returncode=0, duration_s=60.0, threshold_s=30) is False
    assert (
        round_had_no_progress(
            log_dir, returncode=0, duration_s=3.0, threshold_s=30, throttle_active=True
        )
        is False
    )


def test_round_scan_mem_terminated_skips_second_events_tail_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression test for the Task 1 fix-round-1 Important finding: pre-refactor,
    `mem_terminated or _ran_agent_throttled(...)` short-circuited via Python's
    `or`, so a mem-terminated round never triggered the throttle scan at all.
    `serve_cmd._round_scan` must preserve that -- exactly ONE `_tail_events` scan
    (via `round_outcome`) on the mem-terminated path, not a second one from an
    unconditionally-computed `_active_throttles` call."""
    from agent_runner.cli import serve_cmd
    from agent_runner.config import load_config
    from tests._test_helpers import make_toml

    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {"ts": "2026-01-01T00:00:00.000Z", "event": "round_substrate_before", "round_num": 1},
        {"ts": "2026-01-01T00:00:01.000Z", "event": "round_mem_terminated", "round_num": 1},
    )
    cfg = load_config(make_toml(tmp_path))

    calls = 0
    real_tail_events = _throttle._tail_events

    def _counting_tail_events(scanned_log_dir: Path):
        nonlocal calls
        calls += 1
        return real_tail_events(scanned_log_dir)

    monkeypatch.setattr(_throttle, "_tail_events", _counting_tail_events)

    mem_terminated, throttled, outcome = serve_cmd._round_scan(cfg, None, log_dir)

    assert mem_terminated is True
    assert throttled is True
    assert isinstance(outcome, RoundOutcome)
    assert calls == 1


def test_active_throttles_reuses_precomputed_latest_transient_map(tmp_path: Path) -> None:
    """serve_cmd's post-round block passes outcome.latest_transient_per_agent as
    _active_throttles's `_latest` to avoid a second events-tail scan for the
    throttle check -- must give the identical active-throttle map the
    from-scratch scan (`_latest=None`, the default) produces."""
    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {
            "ts": "2026-01-01T00:00:00.000Z",
            "event": "transient_error_detected",
            "agent": "claude",
            "classification": "rate_limit_account",
            "reset_at_epoch": 99999999999,
            "round_num": 1,
        },
    )
    clock = FakeClock(epoch=1000.0)
    outcome = round_outcome(log_dir)
    fresh = _throttle._active_throttles(log_dir, clock=clock)
    reused = _throttle._active_throttles(
        log_dir, clock=clock, _latest=outcome.latest_transient_per_agent
    )
    assert fresh.keys() == reused.keys() == {"claude"}
    assert fresh["claude"] == reused["claude"]


def test_ran_agent_throttled_reuses_precomputed_active_map(tmp_path: Path) -> None:
    """serve_cmd's post-round block passes an already-computed `active` map into
    `_ran_agent_throttled` (via its new `active=` kwarg) instead of letting it
    scan again -- must agree with the from-scratch (`active=None`) call."""
    from agent_runner.cli import serve_cmd
    from agent_runner.config import load_config
    from tests._test_helpers import make_toml

    log_dir = tmp_path / "logs"
    _write(
        log_dir,
        {
            "ts": "2026-01-01T00:00:00.000Z",
            "event": "transient_error_detected",
            "agent": "claude",
            "classification": "rate_limit_account",
            "reset_at_epoch": 99999999999,
            "round_num": 1,
        },
    )
    cfg = load_config(make_toml(tmp_path))
    active = _throttle._active_throttles(log_dir)
    assert serve_cmd._ran_agent_throttled(cfg, None, log_dir, active=active) is True
    assert serve_cmd._ran_agent_throttled(cfg, None, log_dir, active=active) == (
        serve_cmd._ran_agent_throttled(cfg, None, log_dir)
    )
