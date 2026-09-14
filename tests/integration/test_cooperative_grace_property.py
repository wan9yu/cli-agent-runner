"""The v0.3.3 remedy, made into a test for v0.3.5: verify the AGENT actually
experiences the configured grace, not that _kill_pgroup was called with a
particular deadline argument. A real child traps SIGTERM (records it, does
NOT exit), forcing the real killpg(SIGTERM) -> wait -> killpg(SIGKILL)
escalation; the test measures the wall-clock interval and asserts it against
the CONFIGURED grace, both for a cooperative (>= N) and a non-cooperative
(~5s) agent."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_runner import agent_runtime
from agent_runner.clock import SYSTEM_CLOCK

_TOLERANCE_S = 0.5


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _poll_until(predicate, *, timeout_s: float = 15.0, interval_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return bool(predicate())


def _write_sigterm_trapping_script(path: Path, ready_file: Path) -> None:
    """Traps SIGTERM (records nothing needs recording -- the test measures
    from the PARENT side, mirroring _kill_pgroup sending SIGTERM as its very
    first action) and sleeps well past any grace tested here, so it is only
    ever reaped by the SIGKILL escalation, never a clean exit.

    Touches ``ready_file`` only AFTER the trap is installed: a freshly
    ``Popen``'d interpreter needs a moment to import ``signal`` and call
    ``signal.signal(...)`` before the handler actually takes effect, and
    sending SIGTERM into that gap kills the child by the OS default action
    instead of exercising the ignore path -- a real race, reproduced directly
    (measured a spurious near-0s "exited" once in two back-to-back real runs
    with no readiness handshake). The test polls for this file before
    signaling, closing the gap deterministically instead of masking it with a
    fixed sleep."""
    path.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(ready_file)!r}, 'w').write('ready')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )


@pytest.mark.serial
@pytest.mark.timeout(30)
def test_cooperative_agent_should_survive_at_least_its_configured_grace_before_sigkill(tmp_path):
    if not hasattr(os, "killpg"):
        pytest.skip("no killpg on this platform -- POSIX-only property")

    script = tmp_path / "trap.py"
    ready_file = tmp_path / "ready"
    _write_sigterm_trapping_script(script, ready_file)
    proc = subprocess.Popen([sys.executable, str(script)], start_new_session=True)
    pgid = proc.pid

    try:
        assert _poll_until(lambda: ready_file.exists()), (
            "child never installed its SIGTERM trap -- would race the killpg below"
        )

        t0 = time.monotonic()
        agent_runtime._kill_pgroup(proc, clock=SYSTEM_CLOCK, reap_grace_s=2)
        t1 = time.monotonic()

        assert t1 - t0 >= 2 - _TOLERANCE_S, (
            f"agent was killed after only {t1 - t0:.2f}s, short of its configured 2s grace"
        )
        assert _poll_until(lambda: not _alive(pgid), timeout_s=10), (
            "SIGKILL-escalated leader was never actually reaped"
        )
    finally:
        try:
            os.killpg(pgid, 9)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass


@pytest.mark.serial
@pytest.mark.timeout(30)
def test_non_cooperative_agent_should_get_the_default_five_second_grace(tmp_path):
    if not hasattr(os, "killpg"):
        pytest.skip("no killpg on this platform -- POSIX-only property")

    script = tmp_path / "trap.py"
    ready_file = tmp_path / "ready"
    _write_sigterm_trapping_script(script, ready_file)
    proc = subprocess.Popen([sys.executable, str(script)], start_new_session=True)
    pgid = proc.pid

    try:
        assert _poll_until(lambda: ready_file.exists()), (
            "child never installed its SIGTERM trap -- would race the killpg below"
        )

        t0 = time.monotonic()
        agent_runtime._kill_pgroup(proc, clock=SYSTEM_CLOCK)  # default reap_grace_s -> REAP_GRACE_S
        t1 = time.monotonic()

        assert t1 - t0 >= agent_runtime.REAP_GRACE_S - _TOLERANCE_S, (
            f"non-cooperative agent was killed after only {t1 - t0:.2f}s, "
            f"short of the default {agent_runtime.REAP_GRACE_S}s grace"
        )
    finally:
        try:
            os.killpg(pgid, 9)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
