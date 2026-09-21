"""run_bounded is the sanctioned timeout-bounded subprocess primitive --
TERM -> grace -> killpg(SIGKILL) escalation in its own session, so a hung
command leaves no reachable descendants. Never raises on breach; flags
timed_out; bounded even against a session-detached pipe holder."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_runner._bounded import _KILL_GRACE_S, run_bounded


def _wait_until_gone(pid: int, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _wait_for_file(path: Path, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return True
        time.sleep(0.02)
    return False


def test_run_bounded_should_return_output_when_command_succeeds(tmp_path: Path) -> None:
    result = run_bounded(["echo", "hello"], cwd=tmp_path, timeout_s=5)

    assert result.rc == 0
    assert result.stdout.strip() == "hello"

    assert result.timed_out is False


def test_run_bounded_should_reap_and_flag_timed_out_when_command_hangs(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild.pid"
    # The immediate child (sh) is put in its own session by run_bounded's
    # start_new_session=True. It forks a backgrounded marker grandchild that
    # stays in that SAME process group (no setsid of its own) and then hangs
    # forever on `wait` -- exactly the shape of a real hung git subprocess
    # tree. Only a killpg on the whole group reaches the grandchild.
    argv = ["sh", "-c", f"sleep 30 & echo $! > {marker}; wait"]

    result = run_bounded(argv, cwd=tmp_path, timeout_s=1)

    assert result.timed_out is True
    assert marker.exists()
    grandchild_pid = int(marker.read_text().strip())
    assert _wait_until_gone(grandchild_pid), "killpg did not reach the grandchild"


def test_run_bounded_should_stay_bounded_when_a_detached_descendant_holds_the_pipe(
    tmp_path: Path,
) -> None:
    """Bound-under-a-detached-pipe-holder property: a check whose descendant
    ``setsid``-detaches into its OWN
    session (out of the killed process group) but keeps the inherited stdout
    pipe open must not wedge the caller. killpg can't reach that grandchild, so
    the final drain would block on the pipe forever; the primitive must instead
    return within ``timeout_s + 2*_KILL_GRACE_S`` (one grace for TERM->killpg,
    one for the bounded post-kill drain). Wall-clock property test (v0.3.5
    shape): the real clock, not FakeClock -- ``communicate(timeout=)`` self-
    times against the OS clock.

    Mutation check: reverting the post-kill drain to an unbounded
    ``proc.communicate()`` makes ``elapsed`` ~= the descendant's 12s sleep, well
    past the bound below -- the assertion fails.
    """
    # sh backgrounds a python that setsid()s into its own session and sleeps 12s
    # while inheriting stdout, then sh exits 3 immediately. The detached python
    # holds the pipe open long after the (killed) group is gone.
    detached = f"{sys.executable} -c 'import os,time; os.setsid(); time.sleep(12)'"
    argv = ["sh", "-c", f"{detached} & exit 3"]
    timeout_s = 1

    started = time.monotonic()
    result = run_bounded(argv, cwd=tmp_path, timeout_s=timeout_s)
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    bound = timeout_s + 2 * _KILL_GRACE_S
    assert elapsed <= bound + 3.0, (
        f"run_bounded blocked on the detached pipe holder: elapsed {elapsed:.1f}s "
        f"exceeds the {bound}s bound (+slack)"
    )


def test_run_bounded_should_sigkill_the_group_and_reraise_when_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SIGTERM to the supervisor surfaces as a KeyboardInterrupt out of
    ``communicate()`` while the check runs in its own session -- it would never
    be signalled otherwise. run_bounded must SIGKILL the whole check group and
    re-raise, so `systemctl stop` mid-check leaves no orphaned check behind.
    Mutation check: dropping the ``except BaseException`` reap leaves the
    grandchild alive and the liveness assertion below fails.
    """
    marker = tmp_path / "grandchild.pid"
    argv = ["sh", "-c", f"sleep 30 & echo $! > {marker}; wait"]

    real_communicate = subprocess.Popen.communicate
    calls = {"n": 0}

    def fake_communicate(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Let the check actually start and background its grandchild first,
            # then interrupt the wait exactly as install_term_handler would.
            assert _wait_for_file(marker), "check never started"
            raise KeyboardInterrupt("SIGTERM")
        return real_communicate(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", fake_communicate)

    with pytest.raises(KeyboardInterrupt):
        run_bounded(argv, cwd=tmp_path, timeout_s=30)

    grandchild_pid = int(marker.read_text().strip())
    assert _wait_until_gone(grandchild_pid), (
        "the check's process group was not SIGKILLed when the wait was interrupted"
    )
