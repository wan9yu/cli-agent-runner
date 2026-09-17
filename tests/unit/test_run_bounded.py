"""I9: run_bounded is the sanctioned timeout-bounded subprocess primitive --
TERM -> grace -> killpg(SIGKILL) escalation in its own session, so a hung
command leaves no descendants. Never raises on breach; flags timed_out."""

from __future__ import annotations

import os
import time
from pathlib import Path

from agent_runner._bounded import run_bounded


def _wait_until_gone(pid: int, *, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
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
