"""A broker-less FIFO doorbell -- wake a waiting consumer within milliseconds
of a durable event write, instead of a busy-poll loop checking every second.

Per-listener FIFO under ``log_dir/.notify/``: one named pipe per live
``Listener``, path-addressed so the producer (``events.emit``) needs no
registry, no socket, and no persistent connection -- it just fans a single
byte out to every ``*.fifo`` file it finds. Zero cost when nobody is
listening (the directory is empty, ``ring`` is a fast no-op scan).
Self-healing on a stale entry left behind by a crashed listener: opening a
FIFO with no reader raises ``ENXIO``, which ``ring`` treats as "nobody's
there" and cleans up.

Stdlib only (``os``, ``select``, ``pathlib``) -- no watchfiles/inotify,
no socketpair/AF_UNIX. A FIFO is opened ``O_RDWR`` (the self-pipe trick):
holding the write side open ourselves means the read side never sees the
"all writers closed" EOF a plain read-only open would eventually hit.

Fail-open throughout: any ``OSError`` from ``mkfifo``/``open`` degrades a
would-be ``Listener`` to a ``NullListener`` that just sleeps out its
timeout, so a filesystem without FIFO support (or without write
permission under ``log_dir``) never breaks the caller -- it only falls
back to the busy-poll cadence this module was built to avoid.
"""

from __future__ import annotations

import errno
import os
import select
from pathlib import Path

from agent_runner.clock import SYSTEM_CLOCK, Clock

_NOTIFY_DIRNAME = ".notify"


def ring(log_dir: Path) -> None:
    """Best-effort wake for every live ``Listener`` under ``log_dir/.notify/``.

    For each ``*.fifo``: open ``O_WRONLY | O_NONBLOCK``, write one byte,
    close. Every ``OSError`` is swallowed -- ``EAGAIN`` (the listener's
    kernel pipe buffer is full) is harmless, the wake already landed;
    ``ENXIO`` (no reader -- a stale FIFO left by a listener that crashed
    without unlinking) means the entry is dead, so it is unlinked. Never
    raises into the caller's durable-write path.
    """
    notify_dir = log_dir / _NOTIFY_DIRNAME
    try:
        with os.scandir(notify_dir) as it:
            paths = [e.path for e in it if e.name.endswith(".fifo")]
    except OSError:
        return

    for path in paths:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            if e.errno == errno.ENXIO:
                try:
                    os.unlink(path)
                except OSError:
                    pass  # a permission-denied .notify/ can't be cleaned up -- never mind
            continue
        try:
            os.write(fd, b"\x00")
        except OSError:
            pass  # EAGAIN: listener's kernel buffer is full -- the wake already landed
        finally:
            try:
                os.close(fd)
            except OSError:
                pass  # EINTR or similar -- fail-open, never let a close raise into emit()


def drain(fd: int) -> None:
    """Read a level-triggered wakeup fd until ``EAGAIN`` so a delivered byte
    (a ``ring()`` or a ``signal.set_wakeup_fd`` signal) doesn't leave the next
    ``select()`` on it instantly ready -- an undrained fd busy-spins its
    caller at 100% CPU forever after the first wake, since a level-triggered
    fd stays readable until read. Advisory: the bytes carry no payload and are
    discarded. Used only by :meth:`Listener.wait` (its own timeout branch) --
    the pause/sleep waits (``_interruptible_sleep``/``_pause_poll``) are the
    sole consumers of a live ``Listener``. Nothing watches this fd mid-round
    (``_spawn_round`` only ever waits on the round leader's own exit fd), so
    there is no other caller that must drain it itself.
    """
    while True:
        try:
            if not os.read(fd, 4096):
                break
        except OSError:
            break


class Listener:
    """Context manager owning one FIFO under ``log_dir/.notify/``.

    ``__enter__`` creates ``log_dir/.notify/<pid>-<id(self)>.fifo`` and
    opens it ``O_RDWR | O_NONBLOCK`` -- holding the write side ourselves
    is what keeps the read side from ever seeing a persistent EOF. It
    retries up to 3 times if a concurrent ``ring()`` wins the race between
    ``mkfifo`` and ``open`` and unlinks the readerless FIFO out from under
    us (see ``__enter__``'s own comment).
    ``__exit__`` unlinks the FIFO unconditionally (including when an
    exception, e.g. ``KeyboardInterrupt``, propagates through the
    ``with`` block).
    """

    def __init__(self, log_dir: Path) -> None:
        self._notify_dir = log_dir / _NOTIFY_DIRNAME
        self._path = self._notify_dir / f"{os.getpid()}-{id(self)}.fifo"
        self._fd: int | None = None

    def __enter__(self) -> Listener:
        # Idempotent: open_listener() pre-enters a Listener to detect OSError
        # and degrade to a NullListener, then hands the already-entered
        # instance to the caller's own `with` statement, which enters it a
        # second time -- that second entry must be a no-op, not a re-mkfifo.
        if self._fd is not None:
            return self
        self._notify_dir.mkdir(parents=True, exist_ok=True)
        # Bounded retry: mkfifo and open are two separate syscalls, so there is a
        # window between them where the FIFO exists but has no reader yet. A
        # concurrent ring() from another process sharing this log_dir can open
        # our fresh, readerless FIFO O_WRONLY|O_NONBLOCK, get ENXIO, and (by
        # design -- see ring()'s docstring) unlink it as "stale". Our own
        # os.open then loses the file underneath us with FileNotFoundError.
        # Retrying re-creates the FIFO from scratch; three tries makes losing
        # the race every single time astronomically unlikely.
        last_exc: OSError | None = None
        for _ in range(3):
            try:
                os.mkfifo(self._path)
            except FileExistsError:
                pass  # a prior retry (or the same path) already made it -- try to open it
            try:
                self._fd = os.open(self._path, os.O_RDWR | os.O_NONBLOCK)
                return self
            except FileNotFoundError as e:
                last_exc = e  # a concurrent ring() unlinked our readerless FIFO -- retry
        raise last_exc if last_exc is not None else OSError("Listener.__enter__ exhausted retries")

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass  # e.g. a permission-denied .notify/ -- fail-open, never crash teardown

    @property
    def fd(self) -> int:
        """The FIFO's read/write fd -- selectable, and the value handed to
        ``signal.set_wakeup_fd``."""
        assert self._fd is not None, "Listener.fd read outside its `with` block"
        return self._fd

    def wait(self, timeout_s: float, *, clock: Clock = SYSTEM_CLOCK) -> bool:
        """Block until a ring wakes this listener or ``timeout_s`` elapses.

        ``select.select`` against a ``clock.monotonic()`` deadline so a
        real timeout still elapses under real time even though the wake
        itself is an fd event no fake clock can simulate. On wake, drains
        the fd (reads until ``EAGAIN``) so a burst of rings before the
        next ``wait`` call coalesces into a single wake instead of one
        per byte.

        Returns ``True`` iff woken before the timeout -- a ring, a signal
        delivered via ``signal.set_wakeup_fd`` on this same fd, or a
        spurious wake all look identical here; callers never distinguish
        them because the wake is purely advisory -- either way the caller
        re-reads its own state after ``wait`` returns.
        """
        deadline = clock.monotonic() + timeout_s
        remaining = max(0.0, deadline - clock.monotonic())
        ready, _, _ = select.select([self.fd], [], [], remaining)
        if not ready:
            return False

        drain(self.fd)
        return True


class NullListener:
    """Fallback when ``mkfifo``/``open`` fails (no filesystem permission,
    FIFOs unsupported). ``fd`` is ``None`` -- there is nothing to select
    on. A no-op context manager so ``with open_listener(...) as listener``
    works identically whether or not the FIFO could be created.
    """

    @property
    def fd(self) -> None:
        return None

    def __enter__(self) -> NullListener:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> None:
        return None

    def wait(self, timeout_s: float, *, clock: Clock = SYSTEM_CLOCK) -> bool:
        """Degrades to a plain timed sleep -- preserves every ``FakeClock``
        test's semantics unchanged: waiting still advances virtual time by
        exactly ``timeout_s``, same as a bare ``clock.sleep(timeout_s)``."""
        clock.sleep(timeout_s)
        return False


# Stateless (fd=None, wait==clock.sleep, no-op ctx mgr), so one module-level
# instance is a safe default arg -- the stable-singleton default (mirrors
# SYSTEM_CLOCK) every non-serve caller of a listener-aware function keeps by
# not passing one, staying byte-identical to its pre-doorbell behavior.
NULL_LISTENER = NullListener()


def open_listener(log_dir: Path) -> Listener | NullListener:
    """Try to construct a live ``Listener``; degrade to a ``NullListener``
    on any ``OSError`` from ``mkfifo``/``open`` (e.g. a read-only
    filesystem or a platform without FIFO support) so the consumer never
    has to branch on which one it got.

    Pre-enters the ``Listener`` here (rather than leaving that to the
    caller's own ``with``) so the failure can be caught and swapped for a
    ``NullListener``; ``Listener.__enter__`` is idempotent, so the
    caller's subsequent ``with open_listener(log_dir) as listener:`` is
    safe to call it a second time.
    """
    listener = Listener(log_dir)
    try:
        listener.__enter__()
    except OSError:
        try:
            listener._path.unlink(missing_ok=True)
        except OSError:
            pass  # cleanup best-effort -- must not crash the caller's degrade-to-Null path
        return NullListener()
    return listener
