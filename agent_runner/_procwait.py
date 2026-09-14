"""Process-exit waiting on a readable fd, not a busy-poll loop.

``exit_fd`` opens a throwaway fd that becomes readable once a subprocess has
exited (``os.pidfd_open`` on Linux, a kqueue ``NOTE_EXIT`` registration on
macOS/BSD, ``None`` on any other platform or any setup failure). ``wait_exit``
blocks in one ``select.select`` on that fd plus a caller-supplied set of
"wake me early" fds until the process exits, a real deadline passes, or one
of those extra fds fires.

Neither function ever reaps the process on this fast path -- a pidfd or a
kqueue registration only observes the kernel's process table, it never
consumes the zombie slot. There is exactly one path from "notices exit" to
"reaps": the caller, in the same thread, right after ``wait_exit`` returns
``"exited"``. No background watcher thread ever touches the process table,
which is what keeps this immune to CPython's pidfd/child-watcher race
(https://github.com/python/cpython/issues/127049): a background reaper can
consume a zombie's pid slot between the moment some other code decides to
signal that pid and the moment it actually does, letting a freshly spawned,
unrelated process reuse the pid and get signalled by mistake. With no
background reaper at all, that window doesn't exist.

The one exception is the poll fallback used when ``exit_fd`` returns
``None`` (no pidfd/kqueue support, or a kernel/pid-race failure on setup):
it calls ``proc.poll()`` on a short cadence, exactly like the busy-poll loop
this module replaces. That does reap on exit, but synchronously, inside the
one call, in the caller's own thread -- there is still no separate
notice-then-reap window and no background thread, so the safety argument
above still holds; only the fast path is structurally two separate steps
(notice, then a later caller-owned reap).
"""

from __future__ import annotations

import os
import select
import subprocess  # noqa: TID251 -- exit_fd/wait_exit observe Popen.pid; see pyproject.toml
import sys
from typing import Literal

from agent_runner.clock import SYSTEM_CLOCK, Clock

# Fallback poll cadence when exit_fd() is unsupported -- matches the real-second
# value of the busy-poll tick this module replaces
# (agent_runner/cli/_serve_round.py's _ROUND_POLL_TICK_S). Kept as its own
# constant rather than imported so this module has no dependency on
# cli/_serve_round.py; the two simply agree on the same cadence.
_POLL_TICK_S = 1.0


def exit_fd(proc: subprocess.Popen) -> int | None:
    """A fd that becomes readable once ``proc``'s pid has exited (zombie, not
    yet reaped) -- this never itself waits on or reaps it.

    Linux: ``os.pidfd_open(proc.pid, 0)``. macOS/BSD: a kqueue registered
    with ``EVFILT_PROC``/``NOTE_EXIT`` on ``proc.pid``, whose underlying
    kernel object is kept alive by handing back a ``os.dup``'d descriptor
    rather than the kqueue object's own fileno -- the ``select.kqueue``
    Python object is closed before this function returns (its ``__del__``
    would otherwise race a caller's pending ``select`` and close the fd out
    from under it), while the duplicated fd keeps the same underlying kqueue
    alive until the caller closes it. Any other platform, or any ``OSError``
    from either path (kernel too old, permission denied, or the pid already
    reaped out from under us): ``None`` -- always fail-open, never fatal.

    The caller owns the returned fd for exactly the lifetime of the one
    ``wait_exit`` call that opened it; see that function's docstring for the
    close-after-use discipline this enables.
    """
    if sys.platform.startswith("linux") and hasattr(os, "pidfd_open"):
        try:
            return os.pidfd_open(proc.pid, 0)
        except OSError:
            return None

    if hasattr(select, "kqueue"):
        try:
            kq = select.kqueue()
            try:
                event = select.kevent(
                    proc.pid,
                    filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD,
                    fflags=select.KQ_NOTE_EXIT,
                )
                kq.control([event], 0, 0)
                return os.dup(kq.fileno())
            finally:
                kq.close()
        except OSError:
            return None

    return None


def wait_exit(
    proc: subprocess.Popen,
    *,
    deadline: float,
    extra_fds: tuple[int, ...] = (),
    clock: Clock = SYSTEM_CLOCK,
) -> Literal["exited", "timeout", "woken"]:
    """Block until ``proc`` exits, ``deadline`` (a ``clock.monotonic()``
    timestamp) is reached, or a member of ``extra_fds`` becomes readable --
    whichever is soonest. Never calls ``os.waitpid``/``proc.wait``/
    ``proc.poll`` on the fast path -- a caller that gets back ``"exited"``
    must reap ``proc`` itself right after this returns (see the module
    docstring).

    Opens and closes its own ``exit_fd(proc)`` registration internally, once
    per call, in a single ``select.select`` -- callers invoke this at most
    every few seconds (a round's mem-check interval) or once per grace
    window, so the per-call setup cost is negligible and no fd is held open
    across calls.

    Returns ``"exited"`` (the caller should immediately reap, e.g.
    ``proc.wait()``), ``"timeout"`` (deadline reached, proc still running,
    caller re-evaluates its own logic), or ``"woken"`` (an ``extra_fds``
    member fired first -- the caller drains that fd per its own ownership
    contract and loops again with a freshly computed deadline; this changes
    no decision, only how soon the caller re-checks state it was already
    going to re-check).

    Fallback (``exit_fd(proc)`` is ``None``): polls ``proc.poll()`` at a
    short, fixed cadence, still honoring ``extra_fds`` and ``deadline`` each
    tick, with the same three return meanings. This is a real ``select``
    when ``extra_fds`` is non-empty (fd readiness is a real I/O event no
    virtual clock can fake); with no ``extra_fds`` to watch it advances via
    ``clock.sleep`` instead, so a test driving this path with a fake clock
    still makes progress without blocking on real wall time.
    """
    fd = exit_fd(proc)

    if fd is None:
        return _wait_exit_by_polling(proc, deadline=deadline, extra_fds=extra_fds, clock=clock)

    try:
        remaining = max(0.0, deadline - clock.monotonic())
        ready, _, _ = select.select([fd, *extra_fds], [], [], remaining)
        if fd in ready:
            return "exited"
        if ready:
            return "woken"
        return "timeout"
    finally:
        os.close(fd)


def _wait_exit_by_polling(
    proc: subprocess.Popen,
    *,
    deadline: float,
    extra_fds: tuple[int, ...],
    clock: Clock,
) -> Literal["exited", "timeout", "woken"]:
    while True:
        if proc.poll() is not None:
            return "exited"

        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            return "timeout"

        tick = min(_POLL_TICK_S, remaining)
        if extra_fds:
            ready, _, _ = select.select(extra_fds, [], [], tick)
            if ready:
                return "woken"
        else:
            clock.sleep(tick)
