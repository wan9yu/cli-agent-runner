"""b12: serve crash-loop breaker — escalate + stop after consecutive UNKNOWN
short crashes; a clean round resets the run."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from agent_runner.api import CRASH_LOOP_THRESHOLD, PERMANENT_CONFIG_EXIT, post_round_decision
from tests._test_helpers import FakeArgs, make_toml


def _events(log_dir: Path) -> list[dict]:
    out: list[dict] = []
    for f in log_dir.glob("events-*.jsonl"):
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _fake_run(round_returncodes: list[int]):
    """subprocess.run stand-in: only the ROUND subprocess consumes the supplied
    returncodes (repeating the last); git/other calls return 0. Always supplies
    .stdout so compute_git_head etc. don't choke."""
    seq = list(round_returncodes)

    def run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        is_round = isinstance(cmd, (list, tuple)) and "round" in cmd
        if is_round:
            rc = seq.pop(0) if len(seq) > 1 else seq[0]
        else:
            rc = 0
        return type("R", (), {"returncode": rc, "stdout": ""})()

    return run


def test_given_consecutive_short_crashes_when_serve_then_crash_loop_and_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(subprocess, "run", _fake_run([1]))  # always crash, fast

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    assert rc == 0
    crash = [e for e in _events(log_dir) if e.get("event") == "crash_loop"]
    assert len(crash) == 1
    assert crash[0]["consecutive"] == CRASH_LOOP_THRESHOLD


def test_given_clean_rounds_when_serve_then_no_crash_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(subprocess, "run", _fake_run([0]))  # always clean

    serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=4))

    kinds = [e.get("event") for e in _events(log_dir)]
    assert "crash_loop" not in kinds
    assert "max_rounds_reached" in kinds


def test_given_success_between_crashes_when_serve_then_counter_resets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)
    # 3 crashes, a clean round (resets), then crash forever → fires at 5 POST-reset
    monkeypatch.setattr(subprocess, "run", _fake_run([1, 1, 1, 0, 1]))

    serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    crash = [e for e in _events(log_dir) if e.get("event") == "crash_loop"]
    assert len(crash) == 1
    # 5 (post-reset run), not 8 (total crashes) — proves the success reset it.
    assert crash[0]["consecutive"] == CRASH_LOOP_THRESHOLD


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
