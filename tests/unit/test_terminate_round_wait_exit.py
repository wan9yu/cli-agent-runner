"""`_terminate_round`'s grace waits are fd-driven (`wait_exit`), not a busy-poll
`proc.wait(timeout=N)` -- these tests verify the END-TO-END PROPERTY (the
round leader actually gets killed, with the escalation order preserved), not
merely that `wait_exit` was called with the right deadline. Real subprocesses
throughout: a fake proc stub can't prove a real SIGKILL landed."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_runner.cli import _serve_round
from tests._test_helpers import wait_for


def _write_ignore_term_script(path: Path, ready: Path) -> None:
    # Touches `ready` only AFTER the SIG_IGN handler is installed -- .terminate()
    # must never race the child's own startup, or a SIGTERM landing before the
    # handler is armed would kill it under the DEFAULT disposition instead of
    # exercising the ignore-then-escalate path this test targets.
    path.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(ready)!r}, 'w').write('1')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )


def _write_exit_on_term_script(path: Path, ready: Path) -> None:
    # No handler installed -- the default SIGTERM disposition (terminate) fires,
    # which the leader's own `.wait()` observes promptly.
    path.write_text(
        f"import time\nopen({str(ready)!r}, 'w').write('1')\ntime.sleep(30)\n",
        encoding="utf-8",
    )


def test_terminate_round_should_sigkill_after_grace_when_leader_ignores_term(tmp_path, monkeypatch):
    """A leader that SIG_IGNs its own `.terminate()` must be dead -- by SIGKILL,
    after waiting out the full grace window -- not merely signaled. A small
    monkeypatched `_ROUND_TERM_GRACE_S` keeps this fast without weakening the
    property: the wait still genuinely elapses before escalating."""
    monkeypatch.setattr(_serve_round, "_ROUND_TERM_GRACE_S", 0.3)
    ready = tmp_path / "ready"
    script = tmp_path / "ignore_term.py"
    _write_ignore_term_script(script, ready)
    proc = subprocess.Popen([sys.executable, str(script)], start_new_session=True)

    try:
        assert wait_for(tmp_path, ready.exists, timeout_s=20), (
            "child never installed its SIGTERM trap"
        )
        started = time.monotonic()
        rc = _serve_round._terminate_round(proc)
        elapsed = time.monotonic() - started

        assert elapsed >= 0.3, "escalated before the grace window actually elapsed"
        assert elapsed < 5.0, "took far longer than grace + escalation should"
        assert rc == -signal.SIGKILL, f"expected death by SIGKILL, got rc={rc}"
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)  # fully reaped, not merely signaled
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_terminate_round_should_return_rc_when_leader_exits_in_grace(tmp_path, monkeypatch):
    """A leader that dies promptly on TERM (no handler installed) must be
    reaped via the "exited" branch of the FIRST grace wait -- returning its
    real returncode with no killpg escalation at all."""
    monkeypatch.setattr(_serve_round, "_ROUND_TERM_GRACE_S", 5)
    killpg_calls = []
    monkeypatch.setattr(_serve_round.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))
    ready = tmp_path / "ready"
    script = tmp_path / "exit_on_term.py"
    _write_exit_on_term_script(script, ready)
    proc = subprocess.Popen([sys.executable, str(script)], start_new_session=True)

    try:
        assert wait_for(tmp_path, ready.exists, timeout_s=20), "child never started"
        rc = _serve_round._terminate_round(proc)

        assert rc == -signal.SIGTERM, f"expected a clean SIGTERM death, got rc={rc}"
        assert killpg_calls == [], "killpg escalation must not fire when TERM alone reaps"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
