"""B1: agent_runtime.run reaps the agent pgroup on any BaseException (callback
raise or injected signal) instead of orphaning it."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent_runner.agent_runtime import run


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


def test_callback_raise_reaps_agent_pgroup(tmp_path):
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
