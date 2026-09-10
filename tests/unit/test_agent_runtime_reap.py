"""B1: agent_runtime.run reaps the agent pgroup on any BaseException (callback
raise or injected signal) instead of orphaning it."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_runner.agent_runtime import (
    _capture_descendant_pgids,
    _kill_stray_descendants,
    run,
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fake.sh"
    p.write_text(f"#!/bin/bash\nset -e\n{body}\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_agent_pgroup_should_be_reaped_when_progress_callback_raises(tmp_path):
    childpid = tmp_path / "child.pid"
    script = _script(tmp_path, f'sleep 30 & echo $! > "{childpid}"\nwait\n')

    def boom(_stats):
        # Only trigger the reap once the child has recorded its pid. A slow bash
        # startup under load can otherwise let this callback fire (and reap the
        # pgroup) before `echo $! > child.pid` runs, leaving no pidfile for the
        # assertion — a load-dependent flake. Gating the trigger on the pidfile
        # makes the reap happen after the pid is recorded, regardless of load.
        if not childpid.exists() or not childpid.read_text().strip():
            return
        raise OSError("events.emit failed mid-round")

    with pytest.raises(OSError, match="events.emit failed"):
        run(
            work_dir=tmp_path,
            command=[str(script)],
            prompt_arg_template=[],
            prompt="x",
            timeout_s=30,
            log_path=tmp_path / "round.log",
            env_extra={},
            progress_callback=boom,
            progress_interval_s=1,
        )

    pid = int(childpid.read_text())
    for _ in range(80):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid), "agent child was orphaned when the callback raised"


@pytest.mark.timeout(120)  # see the sender wait-budget comment below for the arithmetic
@pytest.mark.serial
def test_agent_pgroup_should_be_reaped_when_sigterm_arrives_during_round(tmp_path):
    """The real SIGTERM path (not a raised-callback stand-in): round_cmd
    installs a handler that converts every SIGTERM into a fresh
    KeyboardInterrupt (round_cmd.py:24-33) so `run`'s BaseException reap path
    fires and drains the agent -- the same mechanism api.kill's sidecar-pid
    TERM-first sequence now drives. Before this task, only the full
    serve+round+agent e2e (test_serve_loop.py) exercised this; no smaller,
    non-e2e test covered it.

    Marked serial: this test sends SIGTERM to the TEST PROCESS ITSELF
    (os.kill(os.getpid(), ...)) via an installed handler. Under pytest-xdist
    every worker is its own process, so this is safe in isolation, but a
    signal handler that stays installed for the process lifetime (or a
    poorly-timed self-signal) risks corrupting whichever OTHER test that
    worker happens to run next -- not worth the risk for one test.

    0.2.19 race fix: the sender thread used to wait a fixed 8s for
    child.pid then send SIGTERM UNCONDITIONALLY, even if the child never
    recorded it under load -- a premature SIGTERM killed bash before it
    wrote the pidfile, and the reap assertion below then blew up with a
    bare FileNotFoundError instead of testing anything. Now the sender only
    signals once ``pid_confirmed`` is actually set, the wait budget is
    widened generously for >=2 concurrent gates, and a genuine expiry (a
    real hang) fails loudly with an explicit message instead of racing a
    blind signal."""
    childpid = tmp_path / "child.pid"
    script = _script(tmp_path, f'sleep 30 & echo $! > "{childpid}"\nwait\n')

    def _raise_term(_sig, _frame):
        raise KeyboardInterrupt("round received SIGTERM")

    old_handler = signal.signal(signal.SIGTERM, _raise_term)

    pid_confirmed = threading.Event()

    def _term_self_once_child_recorded():
        # 45s budget (widened from a fixed 8s, 0.2.19): under >=2 concurrent
        # gates bash's own fork+exec of the backgrounded child (and the
        # `echo $! > child.pid` write) can be starved for many real seconds.
        # Only send SIGTERM once the pid is CONFIRMED on disk -- sending
        # unconditionally after the budget (the old behavior) races bash's
        # own write and can kill it before the pidfile ever lands.
        for _ in range(450):
            if childpid.exists() and childpid.read_text().strip():
                pid_confirmed.set()
                break
            time.sleep(0.1)
        if pid_confirmed.is_set():
            os.kill(os.getpid(), signal.SIGTERM)
        # else: budget genuinely expired -- do NOT send a blind SIGTERM;
        # the explicit assert below fails loudly instead.

    sender = threading.Thread(target=_term_self_once_child_recorded, daemon=True)
    sender.start()

    try:
        raised: KeyboardInterrupt | None = None
        try:
            run(
                work_dir=tmp_path,
                command=[str(script)],
                prompt_arg_template=[],
                prompt="x",
                # 90s (well above the sender's 45s wait budget, with margin
                # for the interrupt to land and be processed): the round
                # must still be in flight when SIGTERM arrives, however late
                # confirmation lands under contention.
                timeout_s=90,
                log_path=tmp_path / "round.log",
                env_extra={},
            )
        except KeyboardInterrupt as exc:
            raised = exc
        sender.join(timeout=5)
        assert pid_confirmed.is_set(), (
            "child never recorded its pid within the 45s wait budget -- "
            "treating this as a genuine hang rather than racing a blind SIGTERM"
        )
        assert raised is not None and "received SIGTERM" in str(raised), (
            f"expected a KeyboardInterrupt from the SIGTERM, got {raised!r}"
        )
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        sender.join(timeout=5)

    assert childpid.exists() and childpid.read_text().strip(), (
        "child.pid missing/empty after the round returned -- SIGTERM raced ahead"
        " of the pidfile write despite pid_confirmed being set"
    )
    pid = int(childpid.read_text())
    for _ in range(80):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid), "agent child was orphaned when SIGTERM interrupted the round"


def _write_detach_script(path: Path, pid_file: Path, sleep_s: int = 60) -> None:
    """A script that setsid()s itself (POSIX: pgid+sid change, ppid unchanged)
    then records its own pid and sleeps -- models a supervised CLI's own
    tty-detach (gemini-cli's detach_from_tty; Claude Code observably:
    anthropics/claude-code #88918/#89275/#91879/#72308 -- a parent/child pair
    holding fds long after the task ended). Its ppid stays pointed at the
    round leader the whole time, but it leaves the leader's process GROUP,
    so os.killpg(leader_pgid, ...) alone never reaches it."""
    path.write_text(
        "import os, time\n"
        "os.setsid()\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"time.sleep({sleep_s})\n",
        encoding="utf-8",
    )


@pytest.mark.serial  # real-subprocess timing (0.2.19 lesson): a double-nested
# python launch racing the round's wall-clock kill is load-sensitive under >=2
# concurrent gates -- run it with no competing xdist workers rather than betting
# the grandchild's setsid+pidfile write always beats the kill under contention.
@pytest.mark.timeout(90)
def test_detached_descendant_should_be_reaped_when_pgroup_is_killed(tmp_path):
    """B(orphan): a round leader whose child setsid()s off the leader's own
    process group (POSIX setsid() changes pgid+sid but NOT ppid) sits outside
    the pgroup `run`'s timeout path killpg's -- before this fix, that
    descendant survived the round's hard-kill, orphaned. `agent_runtime.
    _live_children` (ppid-based, psutil children(recursive=True)) already
    discovers it for the grace busy-check; `_kill_pgroup` must ALSO use that
    walk to target the descendant's own pgid at the hard-kill step.

    timeout_s is generous (well above typical Python startup cost) so the
    detached grandchild reliably exists before the round's wall-clock
    ceiling trips -- a tight bound here would flake under oversubscription,
    not test the fix."""
    grandchild_pid_file = tmp_path / "grandchild.pid"
    detach_py = tmp_path / "detach.py"
    _write_detach_script(detach_py, grandchild_pid_file)
    leader_py = tmp_path / "leader.py"
    leader_py.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(detach_py)!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    result = run(
        work_dir=tmp_path,
        command=[sys.executable, str(leader_py)],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=15,  # serial + generous: the grandchild must be captured before the kill
        log_path=tmp_path / "round.log",
        env_extra={},
    )
    assert result.timed_out

    for _ in range(150):
        if grandchild_pid_file.exists() and grandchild_pid_file.read_text().strip():
            break
        time.sleep(0.1)
    assert grandchild_pid_file.exists(), "detached grandchild never recorded its pid"
    gc_pid = int(grandchild_pid_file.read_text())

    for _ in range(150):
        if not _alive(gc_pid):
            break
        time.sleep(0.1)
    assert not _alive(gc_pid), "detached descendant was orphaned by the timeout hard-kill"


def test_capture_descendant_pgids_should_record_none_when_pid_already_gone(monkeypatch):
    def fake_getpgid(pid):
        if pid == 100:
            return 4242
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", fake_getpgid)
    entries = [{"name": "a", "pid": 100}, {"name": "b", "pid": 200}]

    _capture_descendant_pgids(entries)

    assert entries[0]["pgid"] == 4242
    assert entries[1]["pgid"] is None


def test_kill_stray_descendants_should_signal_captured_pgid_when_pid_still_maps_to_it(monkeypatch):
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "getpgrp", lambda: 999)
    monkeypatch.setattr(os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    _kill_stray_descendants([{"name": "c", "pid": 100, "pgid": 4242}])

    assert killed == [(4242, signal.SIGKILL)]


def test_kill_stray_descendants_should_skip_when_pid_reused_into_a_different_pgid(monkeypatch):
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "getpgrp", lambda: 999)
    monkeypatch.setattr(os, "getpgid", lambda pid: 7777)  # captured 4242, now 7777 -> pid reused
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    _kill_stray_descendants([{"name": "c", "pid": 100, "pgid": 4242}])

    assert killed == []  # live pgid != captured -> never steer SIGKILL onto the wrong group


def test_kill_stray_descendants_should_skip_when_captured_pgid_is_supervisors_own(monkeypatch):
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "getpgrp", lambda: 4242)  # supervisor's own group
    monkeypatch.setattr(os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    _kill_stray_descendants([{"name": "c", "pid": 100, "pgid": 4242}])

    assert killed == []  # never signal agent-runner's own process group


def test_kill_stray_descendants_should_skip_when_pgid_was_none_at_capture(monkeypatch):
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "getpgrp", lambda: 999)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    _kill_stray_descendants([{"name": "c", "pid": 100, "pgid": None}])

    assert killed == []  # gone at capture time -> nothing to target
