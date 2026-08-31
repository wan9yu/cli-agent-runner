from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

from agent_runner import api
from agent_runner.api_types import Alert
from agent_runner.monitor import alert_identity


def _hung(rn: int, elapsed: float) -> Alert:
    return Alert(
        severity="warning",
        detector="hung",
        message="m",
        context={"round_num": rn, "elapsed_s": elapsed, "threshold_s": 2700.0},
        ts="t",
    )


def _disk_warning(value: float) -> Alert:
    return Alert(
        severity="warning",
        detector="disk_warning",
        message="m",
        context={"value": value, "threshold": 90.0},
        ts="t",
    )


def test_same_hung_round_different_elapsed_dedups_to_one_key() -> None:
    # The elapsed_s growing every poll must NOT mint a new key (the ~2880/day spam).
    assert alert_identity(_hung(5, 100.0)) == alert_identity(_hung(5, 130.0))


def test_different_hung_round_is_a_distinct_key() -> None:
    assert alert_identity(_hung(5, 100.0)) != alert_identity(_hung(6, 100.0))


def test_rate_type_alert_keys_on_detector_only() -> None:
    a1 = Alert("warning", "disk_warning", "m", {"value": 91.0, "threshold": 90.0}, "t")
    a2 = Alert("warning", "disk_warning", "m", {"value": 93.0, "threshold": 90.0}, "t")
    assert alert_identity(a1) == alert_identity(a2)


def _write_minimal_monitor_toml(work_dir: Path) -> None:
    """Minimal agent-runner.toml sufficient for load_config inside monitor_loop."""
    prompt_file = work_dir / "prompt.md"
    prompt_file.write_text("p")
    (work_dir / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["-p", "{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{work_dir}"\n'
        f'log_dir = "{work_dir / "logs"}"\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )


def test_active_alert_with_growing_measurement_does_not_spam(tmp_git_repo: Path) -> None:
    """End-to-end through _monitor_loop_iter: the same hung episode polled five
    times with ever-increasing elapsed_s (the real spam shape) must dedup down
    to a single yield; a genuinely different episode still gets through."""
    _write_minimal_monitor_toml(tmp_git_repo)
    polls: Iterator[list[Alert]] = iter(
        [
            [_hung(5, 100.0)],
            [_hung(5, 105.0)],
            [_hung(5, 110.0)],
            [_hung(5, 115.0)],
            [_hung(5, 120.0)],
            [_disk_warning(91.0)],  # a distinct episode — must still surface
        ]
    )
    with (
        patch("agent_runner.clock.SYSTEM_CLOCK.sleep", return_value=None),
        patch("agent_runner.api._poll_once", side_effect=lambda *_a, **_k: next(polls)),
    ):
        gen = api.monitor_loop(tmp_git_repo)
        try:
            first = next(gen)
            second = next(gen)
        finally:
            gen.close()
    assert first.detector == "hung"
    assert second.detector == "disk_warning"


def test_seen_set_evicts_oldest_episode_once_bound_exceeded(tmp_git_repo: Path) -> None:
    """Fill the dedup set past its cap with distinct episodes, then replay the
    very first one — byte-for-byte identical to its first occurrence — followed
    by a guaranteed-fresh sentinel.

    Bounded (correct): round_num=0 was evicted, so the replay is new again and
    is what a single ``next(gen)`` returns.
    Unbounded (buggy): round_num=0's exact context is still remembered forever,
    so the replay is suppressed and the SAME ``next(gen)`` call instead surfaces
    the sentinel — this is what would fail against the pre-fix implementation.
    """
    _write_minimal_monitor_toml(tmp_git_repo)
    cap = api._MONITOR_SEEN_CAP

    def _fill_then_replay() -> Iterator[list[Alert]]:
        for rn in range(cap + 1):
            yield [_hung(rn, 100.0)]
        yield [_hung(0, 100.0)]  # exact replay of the oldest episode
        yield [_disk_warning(91.0)]  # sentinel: always fresh, either code path

    polls = _fill_then_replay()
    with (
        patch("agent_runner.clock.SYSTEM_CLOCK.sleep", return_value=None),
        patch("agent_runner.api._poll_once", side_effect=lambda *_a, **_k: next(polls)),
    ):
        gen = api.monitor_loop(tmp_git_repo)
        try:
            for _ in range(cap + 1):
                next(gen)  # drain the fill phase
            replayed = next(gen)
        finally:
            gen.close()
    assert replayed.detector == "hung"
    assert replayed.context["round_num"] == 0
