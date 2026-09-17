"""Sanctioned timeout-bounded subprocess primitive (I9).

Runs a command in its OWN session under a wall-clock timeout, escalating
TERM -> grace -> killpg(SIGKILL) on breach so a hung process leaves no
descendants (a backgrounded grandchild included, since killpg targets the
whole process group). Returns a ``BoundedResult`` with ``timed_out=True`` on
breach instead of raising -- the caller decides how to react. No ``clock``
parameter: ``subprocess.communicate(timeout=)`` self-times, there is no raw
wall-clock read here to abstract.

Kept out of the startup import graph on purpose (see
tests/invariants/test_import_footprint.py): callers import it function-scope.
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: TID251 — agent_runner._bounded is the sanctioned timeout-bounded subprocess primitive
from dataclasses import dataclass
from pathlib import Path

_KILL_GRACE_S = 3  # grace between TERM and killpg(SIGKILL) on timeout breach


@dataclass(frozen=True)
class BoundedResult:
    rc: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_bounded(argv: list[str], *, cwd: Path, timeout_s: int) -> BoundedResult:
    """Run argv in its own session under a wall-clock timeout, escalating
    TERM -> grace -> killpg on breach so a hung process leaves no descendants.
    Returns timed_out=True on breach -- never raises."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return BoundedResult(rc=proc.returncode, stdout=out, stderr=err, timed_out=False)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            out, err = proc.communicate(timeout=_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            out, err = proc.communicate()
        return BoundedResult(rc=proc.returncode, stdout=out, stderr=err, timed_out=True)
