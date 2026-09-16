"""Git operations — ONLY module that calls git CLI.

Stash safety rules (R820 + §9 IMMUTABLE):
- API is SHA-locked: a stash is identified by the SHA in ``StashRef``, never
  by a stash@{N} index — an index shifts when any stash is pushed or dropped.
- Unified-diff +/-line parsing is forbidden (R2110 lesson): git's diff aligner
  emits cosmetic +/- markers on moved sections, so a +/-scan misclassifies both
  ways. Any auto-tool-vs-human classification must compare line sets vs HEAD.
  Enforced by tests/invariants/test_set_diff_for_auto_tool_classification.py.
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: TID251 — vcs_state.py is the only sanctioned git CLI caller
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from agent_runner import context_store, events
from agent_runner._emit import emit_stale_index_lock_cleared
from agent_runner._serve_policy import EnvironmentalError
from agent_runner.api_types import DirtyOutcome
from agent_runner.clock import SYSTEM_CLOCK

# Fixed git-commit ceiling (plugin-first: no new config knob). Feeds the outer
# round ceiling (api.outer_round_ceiling_s) and is enforced on the commit itself.
GIT_COMMIT_TIMEOUT_S = 120


_GIT_KILL_GRACE_S = 3  # git dies fast on TERM; grace before we killpg the session


class GitTimeout(RuntimeError, EnvironmentalError):  # noqa: N818 — brief-specified name
    """A git invocation exceeded its timeout and was force-killed. Self-heals
    (a hung git process, not a broken config) — classify_round_exit maps this
    to ENV_BATTERY_EXIT, 76 — serve retries at a flat back-off instead of
    counting it as a crash."""


class AutoCommitError(RuntimeError):
    """git add/commit failed during try_auto_commit (reason capped at 200 chars)."""


class StashError(RuntimeError):
    """git stash push failed during stash_orphan (reason capped at 200 chars)."""


@dataclass(frozen=True)
class StashRef:
    sha: str  # full commit SHA — IMMUTABLE under concurrent stash
    message: str  # human-readable label set at creation
    reused: bool = False  # True only when stash_orphan returned an idempotency-window hit


def _run_with_timeout(
    argv: list[str], *, cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess in its OWN session under a wall-clock timeout, escalating
    TERM -> grace -> killpg on breach so a hung git leaves no descendants. Raises
    GitTimeout on breach."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.communicate(timeout=_GIT_KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.communicate()
        raise GitTimeout(f"git {' '.join(argv[1:])[:100]} exceeded {timeout}s") from None


def _git(
    repo: Path,
    *args: str,
    pre_flags: tuple[str, ...] = (),
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    """Single sanctioned wrapper for git CLI invocations.

    Centralises cwd / capture / text / timeout so individual call sites stay
    one-liners and the noqa pragma lives in exactly one place.

    pre_flags are injected between 'git' and command args (e.g. ('-c', 'commit.gpgsign=false')).
    Delegates to ``_run_with_timeout`` so every git call runs in its own session
    with TERM->grace->KILL escalation on timeout (raises GitTimeout, not
    subprocess.TimeoutExpired).
    """
    return _run_with_timeout(["git", *pre_flags, *args], cwd=repo, timeout=timeout)


def is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    r = _git(path, "rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def _porcelain_paths(repo: Path) -> list[str]:
    """Every path with an uncommitted change.

    Uses ``git status --porcelain -z`` (NUL-separated, rename pairs split into
    two records). Returns the new-path side of any rename; old paths are skipped.
    """
    r = _git(repo, "status", "--porcelain", "-z")
    if r.returncode != 0:
        return []
    out: list[str] = []
    records = r.stdout.split("\x00")
    i = 0
    while i < len(records):
        rec = records[i]
        if not rec:
            i += 1
            continue
        if len(rec) < 3:
            i += 1
            continue
        status = rec[:2]
        path = rec[3:]
        # Renames in -z form emit two records: "R  new_path" then "old_path".
        if status[0] == "R" or status[1] == "R":
            out.append(path)
            i += 2  # skip the old_path follow-up record
        else:
            out.append(path)
            i += 1
    return out


def detect_dirty_files(repo: Path) -> list[str]:
    """Files with any uncommitted change."""
    return _porcelain_paths(repo)


def _parse_stash_line(line: str) -> tuple[str, int, str] | None:
    """Parse a ``git stash list --format=%H %ct %s`` line into (sha, ct, msg).

    Strips the ``On <branch>: `` / ``WIP on <branch>: `` prefix from the
    subject so msg is the original message supplied at stash time.
    """
    parts = line.split(" ", 2)
    if len(parts) != 3:
        return None
    sha, ct_s, raw_subj = parts
    try:
        ct = int(ct_s)
    except ValueError:
        return None
    msg = raw_subj.split(": ", 1)[1] if ": " in raw_subj else raw_subj
    return sha, ct, msg


def _recent_orphan_for_round(repo: Path, round_num: int, window_s: int) -> StashRef | None:
    # Only the top stash matters for idempotency; -1 caps git's work as the
    # reflog grows over the project's lifetime.
    r = _git(repo, "stash", "list", "-1", "--format=%H %ct %s")
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parsed = _parse_stash_line(r.stdout.strip().splitlines()[0])
    if parsed is None:
        return None
    sha, ct, msg = parsed
    if not msg.startswith(f"ORPHAN R{round_num}"):
        return None
    if (SYSTEM_CLOCK.epoch() - ct) > window_s:
        return None
    return StashRef(sha=sha, message=msg)


def stash_orphan(
    repo: Path,
    *,
    round_num: int,
    phase: str | None,
    idempotency_s: int = 5,
    log_dir: Path | None = None,
) -> StashRef | None:
    """Stash dirty tree as ORPHAN entry, SHA-locked.

    Returns a ref with ``reused=True`` when a matching ORPHAN was created within
    ``idempotency_s`` (R820 lesson — same-second multiple calls would otherwise
    pile up duplicate stashes); callers distinguish reuse from a fresh stash via
    that flag rather than re-emitting ``orphan_stashed``.

    Returns None on three distinct meanings. The first two are true no-ops:

    - the tree holds no supervisor-owned dirty file;
    - the push stashed nothing because the pathspec excluded everything dirty (a
      round that churned only ``log_dir``) — whether the stash stack is empty or
      an older unrelated stash sits on top;
    - KNOWN GAP: the push succeeded but the follow-up ``git stash list`` failed, so
      the WIP *is* stashed and only its ref was lost — callers still read "nothing
      stashed" and report the tree as ignored. No event kind carries that meaning
      (``orphan_stash_failed`` would be wrong: the stash exists), and naming it is a
      design decision rather than cleanup. Left as-is: a listing that fails
      microseconds after a push that just succeeded in the same repo is effectively
      unreachable.

    Raises StashError when ``git stash push`` itself fails — e.g. intent-to-add
    index entries ("Entry '<f>' not uptodate. Cannot merge."), OR when the push
    times out (``GitTimeout``, translated here): either way callers must not
    read that as a clean tree — the WIP is still on disk. A timed-out push can
    also strand a ``.git/index.lock`` behind our own killed git; that (and only
    that) path clears it, mirroring ``try_auto_commit``'s
    ``_clear_self_caused_index_lock`` use.

    ``log_dir`` (when under ``repo``) is excluded so ``git stash push -u``
    sweeps neither the runner's own bookkeeping (lock / pid / event logs) out
    of the work tree.
    """
    if not detect_dirty_files(repo):
        return None
    existing = _recent_orphan_for_round(repo, round_num, idempotency_s)
    if existing is not None:
        return replace(existing, reused=True)
    ts = SYSTEM_CLOCK.now_utc().strftime("%Y-%m-%dT%H:%M:%S")
    phase_part = f" phase={phase}" if phase else ""
    msg = f"ORPHAN R{round_num}{phase_part} ts={ts}"
    exclude = _combined_exclude_pathspec(repo, log_dir)
    try:
        push = _git(repo, "stash", "push", "-u", "-m", msg, *exclude, timeout=30)
    except GitTimeout as e:
        _clear_self_caused_index_lock(repo, round_num, log_dir)
        raise StashError(str(e)[:200]) from e
    if push.returncode != 0:
        raise StashError((push.stderr or "git stash push failed")[:200])
    listing = _git(repo, "stash", "list", "-1", "--format=%H %s")
    if listing.returncode != 0 or not listing.stdout.strip():
        return None
    sha, _, raw_subj = listing.stdout.strip().partition(" ")
    if msg not in raw_subj:
        # The push stashed nothing (the pathspec excluded everything dirty) and the
        # -1 listing is some older stash — never hand that back as this round's.
        return None
    return StashRef(sha=sha, message=msg)


def _log_dir_exclude_pathspec(root: Path, log_dir: Path | None) -> list[str]:
    """Git pathspec args excluding the runner's own ``log_dir`` from an add/stash,
    applied only when it lives inside the work tree AND is not already gitignored.
    Empty otherwise: an outside or gitignored log_dir is skipped by git's own
    handling, and folding an ignored path into a stash pathspec breaks untracked
    capture (git refuses the ignored path).

    Keeps supervisor bookkeeping (lock / pid / event logs) out of the agent's
    dirty-tree handling: without it a zero-work round's log churn lands in a
    commit (``git_head`` lies) or a ``git stash push -u`` (the logs vanish).

    ``--`` so a leading-dash log_dir is read as a pathname rather than a switch;
    without it git exits 129, which reads here as "not ignored".
    """
    if log_dir is None:
        return []
    try:
        rel = log_dir.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return []  # log_dir outside work_dir → nothing to exclude
    if _git(root, "check-ignore", "-q", "--", rel).returncode == 0:
        return []  # already gitignored → git skips it; pathspec would misfire
    return ["--", f":(exclude){rel}"]


def _combined_exclude_pathspec(root: Path, log_dir: Path | None) -> list[str]:
    """Git pathspec args excluding the runner's own ``log_dir`` from an
    ``add``/``stash`` -- the one exclusion both dirty-action branches
    (``stash_orphan``, ``try_auto_commit``) apply, so the two stay symmetric
    and a future change to the exclusion rule lands in one place.
    """
    return _log_dir_exclude_pathspec(root, log_dir)


def _clear_self_caused_index_lock(work_dir: Path, round_num: int, log_dir: Path | None) -> None:
    """Remove a .git/index.lock our own timed-out+killed git left behind (single
    writer: serve holds the round lock). Emits stale_index_lock_cleared.

    NEVER call this outside the self-caused (our-own-timeout-kill) path — this
    module has no way to distinguish our orphaned lock from one held by a
    concurrent git process, and the single-writer guarantee only holds here
    because we JUST killed the only git invocation serve could have started.
    """
    lock = work_dir / ".git" / "index.lock"
    if not lock.exists():
        return
    lock.unlink(missing_ok=True)
    if log_dir is not None:
        emit_stale_index_lock_cleared(log_dir, lock_path=str(lock), round_num=round_num)


def try_auto_commit(
    work_dir: Path,
    round_num: int,
    phase: str | None,
    *,
    log_dir: Path | None = None,
) -> str:
    """Auto-commit the dirty tree with a hardcoded subject; return the commit SHA.

    Returns "" when nothing remained staged after excluding log_dir (no-op;
    HEAD untouched). Raises AutoCommitError on git failure. DOES NOT push.
    Subject: ``agent-runner auto-commit: R<N> <phase>``. Uses ``git -c
    commit.gpgsign=false``; honors pre-commit hooks (no --no-verify).

    ``log_dir`` (when under ``work_dir``) is excluded from the add, so the
    runner's own bookkeeping is not staged into the agent's auto-commit --
    mirroring ``stash_orphan``.

    The commit call uses GIT_COMMIT_TIMEOUT_S (120s, not the 10s default) since
    real pre-commit hooks routinely exceed 10s. Either the add or the commit
    timing out means OUR git call was killed mid-write, which can leave a
    ``.git/index.lock`` behind; that (and only that) path clears it.
    """
    phase_part = f" {phase}" if phase else ""
    subject = f"agent-runner auto-commit: R{round_num}{phase_part}"

    exclude = _combined_exclude_pathspec(work_dir, log_dir)
    try:
        add_result = _git(work_dir, "add", "-A", *exclude)
    except GitTimeout as e:
        _clear_self_caused_index_lock(work_dir, round_num, log_dir)
        raise AutoCommitError(str(e)[:200]) from e
    if add_result.returncode != 0:
        raise AutoCommitError((add_result.stderr or "git add failed")[:200])

    # Only the exclusion can leave nothing staged (a zero-work round that churned
    # only log_dir); without it the tree was dirty so there is always something
    # to commit. Skip the extra git call on the common (no-exclusion) path.
    if exclude and _git(work_dir, "diff", "--cached", "--quiet").returncode == 0:
        return ""

    try:
        commit_result = _git(
            work_dir,
            "commit",
            "-m",
            subject,
            pre_flags=("-c", "commit.gpgsign=false"),
            timeout=GIT_COMMIT_TIMEOUT_S,
        )
    except GitTimeout as e:
        _clear_self_caused_index_lock(work_dir, round_num, log_dir)
        raise AutoCommitError(str(e)[:200]) from e
    if commit_result.returncode != 0:
        raise AutoCommitError((commit_result.stderr or "git commit failed")[:200])

    head = _git(work_dir, "rev-parse", "HEAD")
    return head.stdout.strip()


def resolve_dirty_tree(
    work_dir: Path,
    dirty_action: Literal["stash", "ignore", "auto_commit"],
    round_num: int,
    phase: str | None,
    log_dir: Path,
    dirty_files: list[str],
    *,
    stash_idempotency_s: int = 5,
) -> DirtyOutcome:
    """Resolve a clean-exit round's dirty working tree per ``[vcs] dirty_action``.

    Folded from the former ``default_dirty_handler`` plugin — dirty-tree policy
    dispatches purely on this one typed config value, so it is plain core, not
    a plugin extension point. Behavior is unchanged: ``"ignore"`` leaves the
    tree dirty; ``"auto_commit"`` commits via :func:`try_auto_commit`, falling
    back to "ignored" (tree left dirty, ``dirty_commit_failed`` emitted) on
    :class:`AutoCommitError` or a no-op commit; anything else (including the
    default ``"stash"``) stashes via :func:`stash_orphan`.
    """
    if dirty_action == "ignore":
        return DirtyOutcome(kind="ignored")
    if dirty_action == "auto_commit":
        try:
            sha = try_auto_commit(work_dir, round_num, phase, log_dir=log_dir)
        except AutoCommitError as exc:
            # Parity with the pre-fold behavior: emit failure event, leave tree
            # dirty (no stash fallback). Dirty tree carries into the next round.
            events.emit(
                log_dir,
                events.DIRTY_COMMIT_FAILED,
                round_num=round_num,
                phase=phase,
                reason=str(exc),
            )
            return DirtyOutcome(kind="ignored")
        if not sha:
            return DirtyOutcome(kind="ignored")
        events.emit(
            log_dir,
            events.DIRTY_AUTO_COMMITTED,
            round_num=round_num,
            files=dirty_files[:20],
            ref=sha,
        )
        return DirtyOutcome(kind="committed", ref=sha)
    return _resolve_stash(
        work_dir,
        round_num,
        phase,
        log_dir,
        dirty_files,
        stash_idempotency_s=stash_idempotency_s,
    )


def _resolve_stash(
    work_dir: Path,
    round_num: int,
    phase: str | None,
    log_dir: Path,
    dirty_files: list[str],
    *,
    stash_idempotency_s: int,
) -> DirtyOutcome:
    try:
        ref = stash_orphan(
            work_dir,
            round_num=round_num,
            phase=phase,
            idempotency_s=stash_idempotency_s,
            log_dir=log_dir,
        )
    except StashError as exc:
        events.emit(
            log_dir,
            events.ORPHAN_STASH_FAILED,
            round_num=round_num,
            phase=phase,
            reason=str(exc),
        )
        return DirtyOutcome(kind="ignored")
    if ref is None:
        return DirtyOutcome(kind="ignored")
    context_store.write_orphan_state(
        log_dir,
        context_store.OrphanState(
            round_num=round_num,
            files=dirty_files,
            stashed_ref=ref.sha,
            stash_message=ref.message,
            timestamp=events.now_iso_ms(),
            phase=phase,
        ),
    )
    events.emit(
        log_dir,
        events.ORPHAN_IDEMPOTENT_SKIP if ref.reused else events.ORPHAN_STASHED,
        round_num=round_num,
        ref=ref.sha,
        reason="clean_exit_with_dirty_tree",
    )
    return DirtyOutcome(kind="stashed", ref=ref.sha)
