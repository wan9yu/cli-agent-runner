"""The v0.3.3 field lesson, made into a test: verify the ACTUAL reap, not the
wait outcome. A review that checks only the mechanism (a wait arg, a call
order) can miss that the property it's supposed to guarantee never actually
holds -- see ``tests/unit/test_kill_pgroup_shielding.py``'s docstring for the
dropped-feature this exact class of gap caused in v0.3.3.

This test spawns a REAL leader that ignores SIGTERM (forcing the killpg
escalation, not the cooperative TERM branch) with a REAL ``setsid()``'d
grandchild (leaves the leader's process group, POSIX ``setsid()`` changes
pgid+sid but not ppid -- exactly the shape ``agent_runtime.
_kill_stray_descendants`` exists to reap), drives them through the real
round-terminate path (``cli._serve_round._terminate_round``, what serve calls
on every round teardown), and after it returns asserts the property directly:

- the leader's process GROUP is gone (``os.killpg(pgid, 0)`` raises ESRCH), AND
- the detached grandchild is gone too (``os.kill(gc_pid, 0)`` raises ESRCH)

-- not merely that a return code looked right or that some internal call was
made. Runs on the REAL per-OS fast path: ``_procwait.exit_fd``/``wait_exit``
are never mocked here (pidfd on Linux, kqueue on macOS/BSD). Only
``_ROUND_TERM_GRACE_S`` -- a policy constant, not the wait mechanism -- is
patched down, so a TERM-ignoring leader doesn't cost the test a real 15s
grace wait.
"""

from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_runner._procwait import exit_fd
from agent_runner.cli import _serve_round


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


def _write_grandchild_script(path: Path, pid_file: Path) -> None:
    """``setsid()``s into its OWN process group before recording its pid, so
    the pidfile only appears once the detach has actually happened -- reading
    it is then a safe signal that ``_snapshot_stray_descendants`` will
    capture the DETACHED pgid, not the leader's."""
    path.write_text(
        "import os, time\n"
        "os.setsid()\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )


def _write_leader_script(path: Path, grandchild_py: Path) -> None:
    """Ignores SIGTERM (forces the terminate path's killpg(SIGKILL)
    escalation) and spawns the setsid'd grandchild before sleeping."""
    path.write_text(
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"subprocess.Popen([sys.executable, {str(grandchild_py)!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )


@pytest.mark.serial  # real double-nested subprocess launch (leader spawns a
# setsid'd child of its own) -- the same load-sensitive timing class
# documented in test_agent_runtime_reap.py's detached-descendant test.
@pytest.mark.timeout(90)
def test_terminate_round_should_reap_pgroup_and_setsid_grandchild_when_leader_ignores_term(
    tmp_path, monkeypatch
):
    if not hasattr(os, "killpg") or not hasattr(os, "setsid"):
        pytest.skip("no killpg/setsid on this platform -- POSIX-only property")

    monkeypatch.setattr(_serve_round, "_ROUND_TERM_GRACE_S", 1)
    grandchild_pid_file = tmp_path / "grandchild.pid"
    grandchild_py = tmp_path / "grandchild.py"
    _write_grandchild_script(grandchild_py, grandchild_pid_file)
    leader_py = tmp_path / "leader.py"
    _write_leader_script(leader_py, grandchild_py)

    proc = subprocess.Popen([sys.executable, str(leader_py)], start_new_session=True)
    pgid = proc.pid

    try:
        probe_fd = exit_fd(proc)
        if probe_fd is None:
            pytest.skip(
                "no pidfd (Linux) / kqueue (macOS-BSD) support on this host -- this test "
                "verifies the fast path specifically, not the poll fallback"
            )
        os.close(probe_fd)

        gc_recorded = _poll_until(
            lambda: grandchild_pid_file.exists() and grandchild_pid_file.read_text().strip() != ""
        )
        assert gc_recorded, "setsid'd grandchild never recorded its pid"
        gc_pid = int(grandchild_pid_file.read_text())
        assert _alive(gc_pid), "grandchild pid recorded but not actually alive to test against"

        rc = _serve_round._terminate_round(proc)

        assert rc != 0, "TERM-ignoring leader must die by SIGKILL, not exit cleanly"
        with pytest.raises((ProcessLookupError, OSError)) as exc_info:
            os.killpg(pgid, 0)
        assert exc_info.value.errno == errno.ESRCH, (
            f"leader's process group {pgid} still has a live member after "
            "_terminate_round returned -- the pgroup was not actually reaped"
        )
        # Poll, don't assert-immediately: _kill_stray_descendants SIGKILLs the
        # grandchild synchronously, but it is the leader's own child, so once the
        # leader is reaped the killed grandchild is reparented to init (ppid 1)
        # and init reaps its zombie asynchronously -- a sub-30ms teardown window
        # in which os.kill(gc_pid, 0) still succeeds on the reaping pid (measured
        # ~19% of runs on a busy host). A genuinely ORPHANED (still-running)
        # grandchild would ride out its full 60s sleep, so this poll fails after
        # the timeout for the real bug while tolerating the reap-teardown race.
        assert _poll_until(lambda: not _alive(gc_pid), timeout_s=10), (
            "setsid()'d grandchild was orphaned by _terminate_round -- it left the leader's "
            "pgroup (killpg(pgid, SIGKILL) alone never reaches it) and must be reaped via "
            "_kill_stray_descendants's captured-pgid path instead"
        )
    finally:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass


@pytest.mark.serial
@pytest.mark.timeout(90)
def test_terminate_round_pid_should_reap_pgroup_and_setsid_grandchild_when_leader_ignores_term(
    tmp_path, monkeypatch
):
    if not hasattr(os, "killpg") or not hasattr(os, "setsid"):
        pytest.skip("no killpg/setsid on this platform -- POSIX-only property")

    from agent_runner import _lifecycle

    monkeypatch.setattr(_lifecycle, "_ROUND_TERM_GRACE_S", 1)
    grandchild_pid_file = tmp_path / "grandchild.pid"
    grandchild_py = tmp_path / "grandchild.py"
    _write_grandchild_script(grandchild_py, grandchild_pid_file)
    leader_py = tmp_path / "leader.py"
    _write_leader_script(leader_py, grandchild_py)

    proc = subprocess.Popen([sys.executable, str(leader_py)], start_new_session=True)
    pgid = proc.pid

    try:
        gc_recorded = _poll_until(
            lambda: grandchild_pid_file.exists() and grandchild_pid_file.read_text().strip() != ""
        )
        assert gc_recorded, "setsid'd grandchild never recorded its pid"
        gc_pid = int(grandchild_pid_file.read_text())
        assert _alive(gc_pid), "grandchild recorded but not alive to test against"

        _lifecycle._terminate_round_pid(proc.pid)

        # _terminate_round_pid only ever holds a bare pid (no Popen handle) and
        # deliberately never reaps the leader itself -- in production a SEPARATE
        # process (serve, or init after reparenting) eventually does that. Here
        # the test IS the real OS parent, so it must reap before checking pgroup
        # liveness: verified directly against a real subprocess on both Linux and
        # macOS, killpg(pgid, 0) against an UNREAPED zombie's group does NOT raise
        # ESRCH regardless of whether the leader already died (Linux: succeeds
        # silently; macOS: raises EPERM) -- reaping first is what makes the ESRCH
        # check below actually test pgroup death, not zombie-vs-permission noise.
        proc.wait(timeout=5)

        with pytest.raises((ProcessLookupError, OSError)) as exc_info:
            os.killpg(pgid, 0)
        assert exc_info.value.errno == errno.ESRCH, (
            f"leader group {pgid} still live after _terminate_round_pid -- pgroup not reaped"
        )
        assert _poll_until(lambda: not _alive(gc_pid), timeout_s=10), (
            "setsid()'d grandchild was orphaned by the out-of-process kill path -- it must be "
            "reaped via _kill_stray_descendants's captured-pgid path on the SIGKILL escalation"
        )
    finally:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
