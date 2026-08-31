"""B3: git runs in its own session with TERM->grace->KILL escalation; a timed-out
commit leaves no surviving index.lock and the next commit succeeds."""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from agent_runner import vcs_state
from tests._test_helpers import read_events_for_current_month


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_run_with_timeout_raises_and_kills(tmp_path):
    started = time.monotonic()
    with pytest.raises(vcs_state.GitTimeout):
        vcs_state._run_with_timeout(["sleep", "30"], cwd=tmp_path, timeout=1)
    assert time.monotonic() - started < 10  # escalated, did not wait out the sleep


def test_commit_timeout_clears_self_caused_lock_and_emits(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("")
    (tmp_path / "f.txt").write_text("x")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    def fake_git(repo, *args, pre_flags=(), timeout=10):
        if args and args[0] == "commit":
            raise vcs_state.GitTimeout("commit exceeded 120s")
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    monkeypatch.setattr(vcs_state, "_git", fake_git)
    with pytest.raises(vcs_state.AutoCommitError):
        vcs_state.try_auto_commit(tmp_path, 7, "dev", log_dir=log_dir)

    assert not lock.exists()
    evs = [
        e
        for e in read_events_for_current_month(log_dir)
        if e.get("event") == "stale_index_lock_cleared"
    ]
    assert len(evs) == 1 and evs[0]["round_num"] == 7


def test_lock_removal_unblocks_next_commit(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".git" / "index.lock").write_text("")  # our timed-out kill left it
    vcs_state._clear_self_caused_index_lock(tmp_path, 1, None)
    sha = vcs_state.try_auto_commit(tmp_path, 1, "dev")
    assert sha  # commit succeeded now the stale lock is gone
