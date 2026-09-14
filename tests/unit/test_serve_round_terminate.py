"""`_terminate_round`'s post-killpg wait must fail open. A D-state
(uninterruptible-sleep) leader can outlive even a killpg SIGKILL, so a bare
`proc.wait(timeout=10)` there would raise `TimeoutExpired` straight back into
`_spawn_round`'s own `except BaseException` cleanup call -- which calls
`_terminate_round` again, raises again, and escapes `cmd()` as an
unclassified exit 1 instead of a defined, classifiable returncode."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_runner import _procwait
from agent_runner.cli import _serve_round
from tests._clock import FakeClock


class _WedgedProc:
    """Never exits -- models a D-state (uninterruptible-sleep) leader that
    outlives even a killpg SIGKILL. ``poll()`` (not ``wait(timeout=)``) is the
    surface ``wait_exit``'s poll fallback drives; this proc always reports
    "still running" so both grace waits in ``_terminate_round`` genuinely
    time out rather than ever observing an exit."""

    pid = 4242
    returncode = None  # live/unreaped, like a running Popen

    def terminate(self):
        pass

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise AssertionError("D-state leader never exits -- wait() must not be reached")


def test_terminate_round_should_return_sentinel_when_leader_is_dstate(monkeypatch):
    """A killpg'd-but-unreapable D-state leader must not raise TimeoutExpired out
    of _terminate_round (which would escape cmd() unclassified as exit 1)."""
    killpg_calls = []
    monkeypatch.setattr(_serve_round.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))
    # The fake pid 4242 must never reach a real psutil.Process() lookup: on a host
    # where 4242 is a live daemon (CI, the Pi hosts), the descendant snapshot could
    # return actual descendants and _kill_stray_descendants would fire a stray
    # killpg, breaking the killpg_calls assertion below. Stub the snapshot empty.
    monkeypatch.setattr(_serve_round, "_snapshot_stray_descendants", lambda proc: [])
    # Force wait_exit's poll fallback (deterministic, no real fd/select
    # dependency on the fake, nonexistent pid 4242) and drive it on a
    # FakeClock so both 15s+10s grace windows resolve instantly, not over
    # 25 real seconds.
    monkeypatch.setattr(_procwait, "exit_fd", lambda proc: None)

    rc = _serve_round._terminate_round(_WedgedProc(), clock=FakeClock())

    assert rc == _serve_round._ROUND_UNREAPED_RC  # a defined sentinel, not a raise
    assert killpg_calls == [(4242, _serve_round.signal.SIGKILL)]  # escalation still fired


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.serial  # real-subprocess reap timing (0.2.19 lesson): consistent with
# its agent_runtime reap twin -- keep it off the parallel gate's oversubscription.
@pytest.mark.timeout(90)
def test_terminate_round_should_reap_detached_descendant_when_leader_ignores_sigterm(
    tmp_path: Path, monkeypatch
):
    """The killpg(SIGKILL) escalation branch (leader ignores `.terminate()`,
    forcing the grace-timeout fallthrough -- mirrors
    test_wedged_round_should_escalate_to_killpg_when_term_is_ignored) must ALSO
    reap a descendant that setsid()'d off the leader's own process group
    (POSIX setsid() changes pgid+sid but NOT ppid): `os.killpg(leader.pid,
    ...)` alone never reaches it, so before this fix it survived the round's
    hard-kill, orphaned. `_ROUND_TERM_GRACE_S` is patched down to 1s purely
    for test speed -- the leader genuinely ignores SIGTERM, so this is a
    deterministic full wait, not a race, and doesn't risk a flake."""
    monkeypatch.setattr(_serve_round, "_ROUND_TERM_GRACE_S", 1)

    grandchild_pid_file = tmp_path / "grandchild.pid"
    detach_py = tmp_path / "detach.py"
    detach_py.write_text(
        "import os, time\n"
        "os.setsid()\n"
        f"open({str(grandchild_pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    leader_py = tmp_path / "leader.py"
    leader_py.write_text(
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"subprocess.Popen([sys.executable, {str(detach_py)!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    proc = subprocess.Popen([sys.executable, str(leader_py)], start_new_session=True)
    gc_pid: int | None = None
    try:
        for _ in range(150):
            if grandchild_pid_file.exists() and grandchild_pid_file.read_text().strip():
                break
            time.sleep(0.1)
        assert grandchild_pid_file.exists(), "detached grandchild never recorded its pid"
        gc_pid = int(grandchild_pid_file.read_text())

        rc = _serve_round._terminate_round(proc)
        assert rc != 0  # died by SIGKILL, not a clean exit

        for _ in range(150):
            if not _alive(gc_pid):
                break
            time.sleep(0.1)
        assert not _alive(gc_pid), (
            "detached descendant was orphaned by _terminate_round's killpg escalation"
        )
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        if gc_pid is not None:
            try:
                os.kill(gc_pid, signal.SIGKILL)
            except OSError:
                pass
