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
    # cmd() installs a real SIGTERM handler; without this no-op each call here
    # would permanently rebind the pytest process's global SIGTERM disposition
    # (the suite runs in one interpreter) — a test-isolation leak, not just a
    # unit-test concern. See test_round_cmd_sigterm.py for the handler's own tests.
    monkeypatch.setattr(round_cmd, "_install_term_handler", lambda: None)
    monkeypatch.setattr(round_cmd, "cfg_from_args_or_config_error", lambda _a: object())
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


# --- Group A: classify_round_exit now governs any exception run_one_round raises ---


def _run_raising(monkeypatch, exc: BaseException) -> int:
    monkeypatch.setattr(round_cmd, "_install_term_handler", lambda: None)
    monkeypatch.setattr(round_cmd, "cfg_from_args_or_config_error", lambda _a: object())

    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(round_cmd, "run_one_round", boom)
    return round_cmd.cmd(Namespace(config="x", phase=None))


def test_config_error_from_run_one_round_returns_78(monkeypatch, capsys):
    """A ConfigError raised by run_one_round itself (e.g. _phase_for's
    stale-serve-cache one) is NOT the cfg-loading ConfigError -- it carries its
    OWN remedy text and goes through the generic traceback branch, not the
    migrate-hint print reserved for cfg_from_args_or_config_error's failures."""
    from agent_runner.config import ConfigError

    assert _run_raising(monkeypatch, ConfigError("bad field")) == 78
    err = capsys.readouterr().err
    assert "bad field" in err  # the exception's own message, via the traceback
    assert "Run `agent-runner migrate`" not in err


def test_lock_held_error_from_run_one_round_returns_76(monkeypatch):
    from agent_runner.runner import LockHeldError

    assert _run_raising(monkeypatch, LockHeldError("another agent-runner is running")) == 76


def test_git_timeout_from_run_one_round_returns_76(monkeypatch):
    from agent_runner.vcs_state import GitTimeout

    assert _run_raising(monkeypatch, GitTimeout("git status exceeded 10s")) == 76


def test_unclassified_exception_returns_1_and_prints_traceback(monkeypatch, capsys):
    rc = _run_raising(monkeypatch, RuntimeError("plugin import blew up"))
    assert rc == 1
    # A classified 78/76 verdict is no less worth diagnosing than a bare 1 --
    # serve captures this subprocess's stderr into round-<N>.log either way.
    assert "RuntimeError" in capsys.readouterr().err


def test_missing_config_file_returns_78(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(round_cmd, "_install_term_handler", lambda: None)
    rc = round_cmd.cmd(Namespace(config=tmp_path / "nope.toml", phase=None))
    assert rc == 78
    # Same friendly, actionable message serve's boot-time ConfigError gets via
    # main()'s handler -- not a raw traceback (round-<N>.log captures stderr).
    err = capsys.readouterr().err
    assert "Run `agent-runner migrate`" in err
    assert "Traceback" not in err


def test_broken_toml_syntax_returns_78(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(round_cmd, "_install_term_handler", lambda: None)
    bad_toml = tmp_path / "agent-runner.toml"
    bad_toml.write_text("this is not [valid toml")
    rc = round_cmd.cmd(Namespace(config=bad_toml, phase=None))
    assert rc == 78
    assert "Run `agent-runner migrate`" in capsys.readouterr().err
