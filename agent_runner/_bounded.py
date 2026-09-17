"""Sanctioned timeout-bounded subprocess primitive.

Runs a command in its OWN session under a wall-clock timeout, escalating
TERM -> grace -> killpg(SIGKILL) on breach so a hung process leaves no
descendants (a backgrounded grandchild included, since killpg targets the
whole process group). Returns a ``BoundedResult`` with ``timed_out=True`` on
breach instead of raising -- the caller decides how to react. No ``clock``
parameter: ``subprocess.communicate(timeout=)`` self-times, there is no raw
wall-clock read here to abstract.

Two hardening properties beyond the plain TERM/KILL escalation:

- **Bounded even against a session-detached pipe holder.** A descendant that
  ``setsid``-detaches out of the killed process group can still hold the
  inherited stdout/stderr pipe open, so the final drain after ``killpg`` would
  block forever waiting on EOF. Every drain is capped at ``_KILL_GRACE_S``; on
  breach the read ends are closed and whatever was captured so far is returned.
  The whole primitive therefore returns within ``timeout_s + 2*_KILL_GRACE_S``
  (one grace for TERM->KILL, one for the post-kill drain) no matter what the
  command spawns. A truly detached grandchild in its OWN session is NOT reaped
  -- killpg cannot reach it; keep checks in a single process group.
- **Reap-on-interrupt.** A SIGTERM to the supervisor (see
  ``cli.common.install_term_handler``, which raises ``KeyboardInterrupt``)
  propagates out of ``communicate()`` while the command runs in its own
  session and would otherwise never be signalled. Any ``BaseException`` out of
  a wait therefore SIGKILLs the whole group and closes the pipes before
  re-raising, mirroring ``agent_runtime.run``'s contract for the agent.

Kept out of the startup import graph on purpose (see
tests/invariants/test_import_footprint.py): callers import it function-scope.
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: TID251 — agent_runner._bounded is the sanctioned timeout-bounded subprocess primitive
from dataclasses import dataclass
from pathlib import Path

_KILL_GRACE_S = 3  # grace between TERM and killpg(SIGKILL), and for the post-kill drain


@dataclass(frozen=True)
class BoundedResult:
    rc: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass


def _close_pipes(proc: subprocess.Popen) -> None:
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass


def _drain_after_kill(proc: subprocess.Popen, exc: subprocess.TimeoutExpired) -> tuple[str, str]:
    """Best-effort drain after ``killpg(SIGKILL)`` that never blocks unbounded.

    A ``setsid``-detached descendant can still hold the stdout/stderr pipe open,
    so stop waiting on it: close the read ends and reap the (already-SIGKILLed)
    immediate child under one more ``_KILL_GRACE_S`` cap. The TimeoutExpired
    from the prior ``communicate`` carries whatever was captured so far on
    POSIX; fall back to empty strings if it did not."""
    _close_pipes(proc)
    try:
        proc.wait(timeout=_KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        pass
    out = exc.output if isinstance(exc.output, str) else ""
    err = exc.stderr if isinstance(exc.stderr, str) else ""
    return out, err


def run_bounded(argv: list[str], *, cwd: Path, timeout_s: int) -> BoundedResult:
    """Run argv in its own session under a wall-clock timeout, escalating
    TERM -> grace -> killpg on breach so a hung command leaves no reachable
    descendants. Returns timed_out=True on breach -- never raises on breach.
    A ``BaseException`` out of a wait (e.g. a SIGTERM-driven KeyboardInterrupt)
    SIGKILLs the group and closes the pipes, then re-raises, so an interrupted
    check is torn down rather than orphaned. ``errors="replace"`` keeps a
    non-UTF-8 byte in the command's output from raising a UnicodeDecodeError
    past the caller's fail-open guard."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        try:
            out, err = proc.communicate(timeout=timeout_s)
            return BoundedResult(rc=proc.returncode, stdout=out, stderr=err, timed_out=False)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                out, err = proc.communicate(timeout=_KILL_GRACE_S)
            except subprocess.TimeoutExpired as exc:
                _kill_group(proc)
                out, err = _drain_after_kill(proc, exc)
            return BoundedResult(rc=proc.returncode, stdout=out, stderr=err, timed_out=True)
    except BaseException:
        _kill_group(proc)
        _close_pipes(proc)
        raise
