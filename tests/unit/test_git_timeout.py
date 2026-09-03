"""B3: git runs in its own session with TERM->grace->KILL escalation; a timed-out
commit leaves no surviving index.lock and the next commit succeeds."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
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


def test_run_with_timeout_escalates_to_killpg_when_term_ignored(tmp_path, monkeypatch):
    """A plain `sleep` dies on the first SIGTERM, so it never exercises the killpg
    branch. A child that IGNORES SIGTERM proves TERM->grace->KILL actually reaches
    the KILL step: (a) GitTimeout still raised, (b) os.killpg(SIGKILL) fired, and
    (c) the process is verifiably dead afterward -- not just terminate()-then-hope."""
    monkeypatch.setattr(vcs_state, "_GIT_KILL_GRACE_S", 1)  # keep the test fast
    calls: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def spy_killpg(pgid: int, sig: int) -> None:
        calls.append((pgid, sig))
        real_killpg(pgid, sig)

    monkeypatch.setattr(vcs_state.os, "killpg", spy_killpg)

    ignore_term = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
    )
    started = time.monotonic()
    with pytest.raises(vcs_state.GitTimeout):
        vcs_state._run_with_timeout([sys.executable, "-c", ignore_term], cwd=tmp_path, timeout=1)
    assert time.monotonic() - started < 10  # escalated, did not wait out the sleep

    assert len(calls) == 1
    pgid, sig = calls[0]
    assert sig == signal.SIGKILL
    assert not _alive(pgid)  # SIGTERM was ignored; only killpg(SIGKILL) could end it


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


def test_foreign_lock_untouched_on_normal_git_failure(tmp_path, monkeypatch):
    """The destructive-action safety crux: an ordinary FAST git failure (e.g. a
    genuinely concurrent process holding index.lock, git exits 128 immediately --
    no timeout involved) must NEVER be treated as self-caused. Only a GitTimeout
    triggers _clear_self_caused_index_lock; a ``CompletedProcess`` with a nonzero
    returncode is a completely different code path and must leave the lock alone."""
    (tmp_path / ".git").mkdir()
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("")  # a lock some OTHER process holds -- not ours to touch
    (tmp_path / "f.txt").write_text("x")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    def fake_git(repo, *args, pre_flags=(), timeout=10):
        if args and args[0] == "commit":
            return subprocess.CompletedProcess(
                ["git", *args],
                128,
                "",
                "fatal: Unable to create '.../index.lock': File exists.",
            )
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    monkeypatch.setattr(vcs_state, "_git", fake_git)
    with pytest.raises(vcs_state.AutoCommitError):
        vcs_state.try_auto_commit(tmp_path, 7, "dev", log_dir=log_dir)

    assert lock.exists()  # foreign lock: untouched -- no GitTimeout occurred
    # try_auto_commit itself emits nothing on a plain AutoCommitError, so the
    # events file may not exist at all -- that absence IS the assertion.
    events_files = list(log_dir.glob("events-*.jsonl"))
    evs = (
        [
            e
            for e in read_events_for_current_month(log_dir)
            if e.get("event") == "stale_index_lock_cleared"
        ]
        if events_files
        else []
    )
    assert evs == []


def test_stash_push_timeout_clears_self_caused_lock_and_raises_stash_error(tmp_path, monkeypatch):
    """Mirrors test_commit_timeout_clears_self_caused_lock_and_emits for
    stash_orphan: a killed `git stash push` can strand .git/index.lock exactly
    like a killed commit, so it must get the same self-caused-lock clearing +
    typed-error translation (StashError, not a bare GitTimeout) so the dirty
    handler can emit ORPHAN_STASH_FAILED cleanly instead of a generic
    hook_failed."""
    (tmp_path / ".git").mkdir()
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("")
    (tmp_path / "f.txt").write_text("x")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    def fake_git(repo, *args, pre_flags=(), timeout=10):
        if args and args[0] == "status":
            return subprocess.CompletedProcess(["git", *args], 0, "?? f.txt\x00", "")
        if args[:2] == ("stash", "list"):
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        if args[:2] == ("stash", "push"):
            raise vcs_state.GitTimeout("stash push exceeded 30s")
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    monkeypatch.setattr(vcs_state, "_git", fake_git)
    with pytest.raises(vcs_state.StashError):
        vcs_state.stash_orphan(tmp_path, round_num=7, phase="dev", log_dir=log_dir)

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
