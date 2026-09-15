"""Process-exit waiting on a readable fd, not a busy-poll loop.

``exit_fd`` opens a throwaway fd that becomes readable once a subprocess has
exited (``os.pidfd_open`` on Linux, a kqueue ``NOTE_EXIT`` registration on
macOS/BSD, ``None`` on any other platform or any setup failure). ``wait_exit``
blocks in one ``select.select`` on that fd until the process exits or a real
deadline passes.

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

# Fallback poll cadence when exit_fd() is unsupported -- matches the
# real-second value of the busy-poll tick this module's callers used to run
# themselves before switching to wait_exit (e.g. _spawn_round's old
# proc.wait(timeout=1)). Kept as its own constant, not imported by/from a
# caller, so this module has no dependency on any of them.
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
    clock: Clock = SYSTEM_CLOCK,
    wake_fd: int | None = None,
) -> Literal["exited", "timeout", "woken"]:
    """Block until ``proc`` exits, ``deadline`` (a ``clock.monotonic()``
    timestamp) is reached, or ``wake_fd`` becomes readable -- whichever is
    soonest. Never calls ``os.waitpid``/``proc.wait``/``proc.poll`` on the
    fast path -- a caller that gets back ``"exited"`` must reap ``proc``
    itself right after this returns (see the module docstring).

    Opens and closes its own ``exit_fd(proc)`` registration internally, once
    per call, in a single ``select.select`` -- callers invoke this at most
    every few seconds (a round's mem-check interval) or once per grace
    window, so the per-call setup cost is negligible and no fd is held open
    across calls.

    Returns ``"exited"`` (the caller should immediately reap, e.g.
    ``proc.wait()``), ``"timeout"`` (deadline reached, proc still running,
    caller re-evaluates its own logic), or ``"woken"`` (only possible when
    ``wake_fd`` is given: it became readable before either of the other two).
    When ``proc`` has already exited by the time ``deadline`` is reached, the
    exit fd is ready in the SAME ``select`` call that also times out -- the
    exit always wins that tie, so an already-dead proc is never mistakenly
    reported as ``"timeout"``. Likewise, when both the exit fd and
    ``wake_fd`` are ready in the same ``select`` call, the exit fd wins --
    an already-dead proc is never mistakenly reported as ``"woken"``.
    ``wake_fd`` is caller-owned: this function neither opens, closes, nor
    drains it (the caller drains it after a ``"woken"`` return); ``_procwait``
    never imports ``_notify``.

    Fallback (``exit_fd(proc)`` is ``None``): polls ``proc.poll()`` at a
    short, fixed cadence via ``clock.sleep``, with the same two return
    meanings (``wake_fd`` is IGNORED on this path -- a documented latency
    degrade, not a "woken" report), so a test driving this path with a fake
    clock still makes progress without blocking on real wall time. The same
    poll fallback is also used when the fast path's ``select.select`` raises
    ``ValueError`` (a fd >= ``FD_SETSIZE``, 1024, is unselectable) instead of
    crashing.

    Short-circuits to ``"exited"`` when ``proc.returncode`` is already set:
    this owner has already reaped it, so its pid is freed (and may be reused).
    Re-opening ``exit_fd(proc.pid)`` on a freed pid could bind pidfd/kqueue to
    an UNRELATED process and ride out the whole deadline as a false
    ``"timeout"``. The most common way this happens is ``Popen.terminate()``,
    whose ``send_signal`` calls ``proc.poll()`` first and reaps a leader that
    exited in the microseconds before the signal.
    """
    if proc.returncode is not None:
        return "exited"  # already reaped by this owner -- never re-open a pid we no longer hold

    fd = exit_fd(proc)

    if fd is None:
        return _wait_exit_by_polling(proc, deadline=deadline, clock=clock)

    try:
        remaining = max(0.0, deadline - clock.monotonic())
        rlist = [fd, wake_fd] if wake_fd is not None else [fd]
        try:
            ready, _, _ = select.select(rlist, [], [], remaining)
        except ValueError:
            # fd >= FD_SETSIZE (1024) makes select.select raise ValueError --
            # degrade to the poll fallback (its documented latency) instead
            # of crashing.
            return _wait_exit_by_polling(proc, deadline=deadline, clock=clock)
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
    clock: Clock,
) -> Literal["exited", "timeout"]:
    # Not clock.wait_until: keep this poll loop in _procwait so
    # test_procwait_shield.py's _procwait-only scan guards its KeyboardInterrupt
    # propagation (the grace-kill shield depends on it).
    while True:
        if proc.poll() is not None:
            return "exited"

        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            return "timeout"

        clock.sleep(min(_POLL_TICK_S, remaining))
