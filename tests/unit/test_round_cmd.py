"""round_cmd maps the round outcome to an exit code so serve's crash-loop breaker
(which keys on this subprocess's returncode) can see a real agent crash. A
grace-kill or timeout is NOT a crash; the serve-reserved 78/75 are never returned."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from agent_runner.api_types import RoundResult
from agent_runner.cli import round_cmd


def _rr(exit_code: int, *, timed_out: bool = False, killed_for_grace: bool = False) -> RoundResult:
    return RoundResult(
        round_num=1,
        phase=None,
        started_at="",
        ended_at="",
        exit_code=exit_code,
        duration_s=1.0,
        timed_out=timed_out,
        log_path=Path("x"),
        dirty_files=[],
        stashed=False,
        killed_for_grace=killed_for_grace,
    )


def _run(monkeypatch, result: RoundResult) -> int:
    monkeypatch.setattr(round_cmd, "cfg_from_args", lambda _a: object())
    with patch.object(round_cmd, "run_one_round", return_value=result):
        return round_cmd.cmd(Namespace(config="x", phase=None))


def test_agent_crash_returns_1(monkeypatch):
    assert _run(monkeypatch, _rr(1)) == 1  # non-zero agent exit → breaker sees it


def test_clean_round_returns_0(monkeypatch):
    assert _run(monkeypatch, _rr(0)) == 0


def test_grace_kill_returns_0(monkeypatch):
    # agent produced a result then lingered → grace-killed, not a crash
    assert _run(monkeypatch, _rr(-15, killed_for_grace=True)) == 0


def test_timeout_returns_0(monkeypatch):
    # a wall-clock timeout is not a crash-loop signal (it's long, not a short crash)
    assert _run(monkeypatch, _rr(-15, timed_out=True)) == 0
