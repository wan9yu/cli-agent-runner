"""b12: serve crash-loop breaker — escalate + stop after consecutive UNKNOWN
short crashes; a clean round resets the run."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_runner._serve_policy import (
    _MEM_LOOP_PERSIST_THRESHOLD,
    _MEM_LOOP_PERSIST_WINDOW_S,
    MEM_LOOP_EXIT,
    MEM_LOOP_PERSISTENT_EXIT,
    MEM_LOOP_THRESHOLD,
)
from agent_runner.api import (
    CRASH_LOOP_EXIT,
    CRASH_LOOP_THRESHOLD,
    ENV_BATTERY_EXIT,
    PERMANENT_CONFIG_EXIT,
    post_round_decision,
)
from tests._test_helpers import FakeArgs, make_toml, read_events_for_current_month


def _fake_spawn(round_returncodes: list[int]):
    """serve_cmd._spawn_round stand-in: returns the supplied returncodes in
    sequence (repeating the last), and writes the round log file so downstream
    readers (round-current.log relink, crash_loop's log tail) don't choke."""
    seq = list(round_returncodes)

    def spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        rc = seq.pop(0) if len(seq) > 1 else seq[0]
        round_log_path.write_text("round output\n")
        return rc

    return spawn


def _fake_spawn_mem_terminated():
    """serve_cmd._spawn_round stand-in: every round is killed by the mid-round
    memory-pressure hard floor -- emits round_mem_terminated (like the real
    _spawn_round does on critical pressure) and returns a short, non-zero
    signal-death-style returncode."""
    from agent_runner.api import emit_round_mem_terminated

    def spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        round_log_path.write_text("round output\n")
        emit_round_mem_terminated(
            round_log_path.parent,
            pid=12345,
            severity="critical",
            signal="swap_out_rate",
            message="swap sout +2147483648B since last sample (sustained heavy paging)",
            consecutive=3,
            context={"swap_sout_delta": 2147483648},
        )
        return -15

    return spawn


def test_given_consecutive_short_crashes_when_serve_then_crash_loop_and_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn([1]))  # always crash, fast

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    assert rc == CRASH_LOOP_EXIT
    crash = [e for e in read_events_for_current_month(log_dir) if e.get("event") == "crash_loop"]
    assert len(crash) == 1
    assert crash[0]["consecutive"] == CRASH_LOOP_THRESHOLD


def test_given_clean_rounds_when_serve_then_no_crash_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn([0]))  # always clean

    serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=4))

    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "crash_loop" not in kinds
    assert "max_rounds_reached" in kinds


def test_given_consecutive_env_battery_exits_when_serve_then_no_crash_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ~5-round environmental outage (76, mirrors an active throttle) must not
    trip the crash-loop breaker — it should keep restarting with an escalating
    delay until max_rounds, never emitting crash_loop."""
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn([ENV_BATTERY_EXIT]))

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=6))

    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "crash_loop" not in kinds
    assert "max_rounds_reached" in kinds
    assert rc == 0


def test_given_success_between_crashes_when_serve_then_counter_resets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    # 3 crashes, a clean round (resets), then crash forever → fires at 5 POST-reset
    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn([1, 1, 1, 0, 1]))

    serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    crash = [e for e in read_events_for_current_month(log_dir) if e.get("event") == "crash_loop"]
    assert len(crash) == 1
    # 5 (post-reset run), not 8 (total crashes) — proves the success reset it.
    assert crash[0]["consecutive"] == CRASH_LOOP_THRESHOLD


def test_mem_terminations_under_threshold_no_crash_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A round killed by the mid-round memory-pressure hard floor is a
    coma-prevented retry, not a crash: the floor can fire within ~10s (its own
    sample cadence), far under the crash-loop breaker's 60s short-crash
    window, so unlike a wall-clock-ceiling wedge (whose long duration alone
    dodges the breaker) this needs an explicit exemption. Below
    MEM_LOOP_THRESHOLD consecutive mem-terminations, serve neither trips
    crash_loop nor gives up via mem_loop -- it keeps retrying at the doubled
    back-off, exactly like an active throttle."""
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_mem_terminated())
    rounds = MEM_LOOP_THRESHOLD - 1

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=rounds))

    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "crash_loop" not in kinds
    assert "mem_loop" not in kinds
    assert kinds.count("round_mem_terminated") == rounds
    assert "max_rounds_reached" in kinds
    assert rc == 0


def test_mem_terminations_at_threshold_gives_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """MEM_LOOP_THRESHOLD consecutive mem-terminated rounds means the host is
    NOT recovering -- serve gives up via mem_loop (exit 71) rather than
    looping forever, distinct from crash_loop: it is RESTARTABLE (systemd
    brings it back fresh), unlike config_broken/crash_loop's deliberate stop,
    because the underlying host-memory condition can clear between serve
    process restarts even when it hasn't cleared between rounds."""
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_mem_terminated())

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=MEM_LOOP_THRESHOLD + 3))

    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "mem_loop" in kinds
    assert "crash_loop" not in kinds
    assert kinds.count("round_mem_terminated") == MEM_LOOP_THRESHOLD
    assert rc == MEM_LOOP_EXIT


def _seed_mem_loop_events(log_dir: Path, count: int, *, ago_s: float) -> None:
    """Write ``count`` prior ``mem_loop`` events directly into this month's
    events file, each stamped ``ago_s`` seconds before real now -- exercises
    ``mem_loop_events_in_window``'s real-``SYSTEM_CLOCK`` read through the
    full ``serve_cmd.cmd()`` loop without needing a FakeClock end to end."""
    log_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    stamp_dt = datetime.fromtimestamp(now.timestamp() - ago_s, UTC)
    stamp = stamp_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    path = log_dir / f"events-{now.strftime('%Y-%m')}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for _ in range(count):
            line = {
                "ts": stamp,
                "event": "mem_loop",
                "consecutive": 5,
                "exit_code": -15,
                "reason": "",
            }
            f.write(json.dumps(line) + "\n")


def test_mem_loop_escalates_to_persistent_after_window_threshold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """0.2.16 Task 5: mem_loop alone (71) resets on every serve restart, so a
    host stuck under sustained pressure respawns into the identical
    break-then-restart loop forever. 2 prior mem_loop episodes seeded well
    within the escalation window + this run's own 5 mem-terminated rounds
    (its own would-be 3rd mem_loop, == _MEM_LOOP_PERSIST_THRESHOLD) means
    serve STOPS for real instead: mem_loop_persistent, exit 70."""
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    _seed_mem_loop_events(log_dir, _MEM_LOOP_PERSIST_THRESHOLD - 1, ago_s=60)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_mem_terminated())

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=MEM_LOOP_THRESHOLD + 3))

    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "mem_loop_persistent" in kinds
    assert kinds.count("mem_loop") == _MEM_LOOP_PERSIST_THRESHOLD - 1  # only the seeded ones
    assert rc == MEM_LOOP_PERSISTENT_EXIT


def test_mem_loop_still_71_when_prior_events_aged_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same 2 prior mem_loop episodes, but stamped OUTSIDE
    _MEM_LOOP_PERSIST_WINDOW_S -- they've aged out, so this run's mem_loop is
    (correctly) treated as the 1st in-window occurrence and stays the usual
    restartable 71, not the persistent stop."""
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    _seed_mem_loop_events(
        log_dir, _MEM_LOOP_PERSIST_THRESHOLD - 1, ago_s=_MEM_LOOP_PERSIST_WINDOW_S + 300
    )
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(serve_cmd, "_spawn_round", _fake_spawn_mem_terminated())

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=MEM_LOOP_THRESHOLD + 3))

    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "mem_loop_persistent" not in kinds
    assert kinds.count("mem_loop") == _MEM_LOOP_PERSIST_THRESHOLD  # 2 stale seeded + this run's
    assert rc == MEM_LOOP_EXIT


def test_mem_loop_events_in_window_counts_recent_excludes_old(tmp_path: Path) -> None:
    """Direct unit test of the events-tail helper feeding the escalation
    above: a FakeClock pins "now" so a prior event just inside the window
    counts and one just outside does not, with no dependency on real time."""
    from agent_runner._throttle import mem_loop_events_in_window
    from tests._clock import FakeClock

    log_dir = tmp_path / "logs"
    clock = FakeClock(epoch=2_000_000_000.0)
    _seed_mem_loop_events_at(log_dir, epoch=clock.epoch() - 60)
    _seed_mem_loop_events_at(log_dir, epoch=clock.epoch() - _MEM_LOOP_PERSIST_WINDOW_S - 60)

    assert mem_loop_events_in_window(log_dir, clock, _MEM_LOOP_PERSIST_WINDOW_S) == 1


def _seed_mem_loop_events_at(log_dir: Path, *, epoch: float) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp_dt = datetime.fromtimestamp(epoch, UTC)
    stamp = stamp_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    path = log_dir / f"events-{stamp_dt.strftime('%Y-%m')}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        line = {"ts": stamp, "event": "mem_loop", "consecutive": 5, "exit_code": -15, "reason": ""}
        f.write(json.dumps(line) + "\n")


# --- pure-function tests for the extracted restart policy ---


def test_post_round_decision_config_broken_routes_to_stop() -> None:
    action, _, _ = post_round_decision(
        returncode=PERMANENT_CONFIG_EXIT,
        duration_s=0.1,
        throttle_active=False,
        consecutive=0,
        restart_delay_s=3,
    )
    assert action == "config_broken"


def test_post_round_decision_clean_round_resets_and_continues() -> None:
    action, delay, n = post_round_decision(
        returncode=0, duration_s=0.1, throttle_active=False, consecutive=4, restart_delay_s=3
    )
    assert (action, delay, n) == ("continue", 3, 0)


def test_post_round_decision_short_crash_escalates_then_stops() -> None:
    a4, d4, n4 = post_round_decision(
        returncode=1, duration_s=0.1, throttle_active=False, consecutive=3, restart_delay_s=3
    )
    assert (a4, d4, n4) == ("continue", 3 * 2**4, 4)
    a5, _, n5 = post_round_decision(
        returncode=1, duration_s=0.1, throttle_active=False, consecutive=4, restart_delay_s=3
    )
    assert (a5, n5) == ("crash_loop", CRASH_LOOP_THRESHOLD)


def test_post_round_decision_transient_or_long_failure_is_not_a_crash() -> None:
    # classified transient (throttle active): not a crash → reset
    assert post_round_decision(
        returncode=1, duration_s=0.1, throttle_active=True, consecutive=2, restart_delay_s=3
    ) == ("continue", 6, 0)
    # long-running failure: not a tight crash loop → reset, 2x delay
    assert post_round_decision(
        returncode=1, duration_s=999.0, throttle_active=False, consecutive=2, restart_delay_s=3
    ) == ("continue", 6, 0)
