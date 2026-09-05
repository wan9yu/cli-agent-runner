"""B2: a round subprocess that blows past the outer ceiling is TERM'd (its own
handler reaping any agent) → killpg only if it ignores TERM; round_supervisor_wedged
is emitted; the returncode flows back to serve."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agent_runner.cli import _serve_round, serve_cmd
from tests._test_helpers import read_events_for_current_month


def test_spawn_round_wedged_terminates_and_emits(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # A round leader that ignores nothing: a plain sleeper. timeout_s is tiny so the
    # ceiling trips; SIGTERM (proc.terminate) reaps it well before killpg.
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    rc = serve_cmd._spawn_round(
        argv,
        log_dir / "round-1.log",
        {},
        timeout_s=1,
    )
    assert rc != 0  # died by signal, not a clean 0
    wedged = [
        e
        for e in read_events_for_current_month(log_dir)
        if e.get("event") == "round_supervisor_wedged"
    ]
    assert len(wedged) == 1
    assert wedged[0]["timeout_s"] == 1
    assert (log_dir / "round-1.log").exists()

    from agent_runner.agent_runtime import REAP_GRACE_S

    assert _serve_round._ROUND_TERM_GRACE_S >= REAP_GRACE_S


def test_spawn_round_wedged_escalates_to_killpg_when_term_ignored(tmp_path, monkeypatch):
    """The load-bearing safety net: a round leader that TRAPS SIGTERM (a real hang,
    not a cooperative one) forces `_terminate_round` past its TERM-then-wait branch
    into the killpg(SIGKILL) escalation. Spies on Popen.terminate + os.killpg (both
    call through to the real implementation) to prove the ORDER is TERM-first, then
    killpg — not a bare killpg. `_ROUND_TERM_GRACE_S` is patched down to 1s so the
    grace wait doesn't slow the test; the escalation logic itself is untouched."""
    monkeypatch.setattr(_serve_round, "_ROUND_TERM_GRACE_S", 1)

    call_order: list[str] = []
    original_terminate = subprocess.Popen.terminate
    original_killpg = os.killpg

    def spy_terminate(self):
        call_order.append("terminate")
        return original_terminate(self)

    def spy_killpg(pgid, sig):
        call_order.append("killpg")
        return original_killpg(pgid, sig)

    monkeypatch.setattr(subprocess.Popen, "terminate", spy_terminate)
    monkeypatch.setattr(os, "killpg", spy_killpg)

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Installs SIG_IGN for SIGTERM before sleeping — proc.terminate() (SIGTERM) is a
    # no-op against it, so only SIGKILL (via killpg) can end it.
    argv = [
        sys.executable,
        "-c",
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n",
    ]
    rc = serve_cmd._spawn_round(argv, log_dir / "round-1.log", {}, timeout_s=1)

    assert call_order == ["terminate", "killpg"]  # TERM tried first, killpg last resort
    assert rc < 0, "expected death by SIGKILL, not a clean exit"

    wedged = [
        e
        for e in read_events_for_current_month(log_dir)
        if e.get("event") == "round_supervisor_wedged"
    ]
    assert len(wedged) == 1  # still emitted exactly once even though TERM alone failed

    # No orphan: the process (its own session/pgroup leader) is fully reaped, not
    # merely signaled — _terminate_round's post-killpg proc.wait() confirms exit.
    with pytest.raises(ProcessLookupError):
        os.kill(wedged[0]["pid"], 0)
