"""Git operations — ONLY module that calls git CLI.

Stash safety rules (R820 + §9 IMMUTABLE):
- API is SHA-locked: callers pass and store SHA only, never stash@{N} index.
  Internal drop/pop translate SHA -> current selector immediately before each
  git call. Safe against caller-side index drift; single-supervisor-per-repo
  design means external concurrent ``git stash push`` is not a defended scenario.
- "Auto-tool change vs human change" detection uses set-based diff vs HEAD,
  not unified-diff +/-line parsing (R2110 lesson).
"""

from __future__ import annotations

import subprocess  # noqa: TID251 — vcs_state.py is the only sanctioned git CLI caller
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StashRef:
    sha: str       # full commit SHA — IMMUTABLE under concurrent stash
    message: str   # human-readable label set at creation


def is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    r = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def detect_dirty_files(repo: Path) -> list[str]:
    """Return list of files with any uncommitted change (modified / untracked / renamed).

    Uses ``git status --porcelain -z`` (NUL-separated, rename pairs split into
    two records). Returns the new-path side of any rename; old paths are skipped.
    """
    r = subprocess.run(
        ["git", "status", "--porcelain", "-z"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
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


def set_diff_vs_head(repo: Path, path: Path) -> set[str]:
    """Lines present in working-tree path but absent from HEAD:path.

    Uses set comparison, NOT unified-diff +/-line parsing. R2110 lesson:
    git's diff aligner emits cosmetic +/- markers when sections move, so
    +/-line scanning produces both false positives (mis-classifies real
    edits as automated) and false negatives (mis-classifies repeated
    headings as user edits). Set comparison ignores all alignment noise.

    :param path: file path relative to ``repo`` root (joined as ``repo / path``).
    """
    head = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if head.returncode != 0:
        return set()
    wt_path = repo / path
    if not wt_path.exists():
        return set()
    head_lines = set(head.stdout.splitlines())
    wt_lines = set(wt_path.read_text(encoding="utf-8").splitlines())
    return wt_lines - head_lines


def list_recent_stashes(repo: Path) -> list[StashRef]:
    r = subprocess.run(
        ["git", "stash", "list", "--format=%H %ct %s"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return []
    out: list[StashRef] = []
    for line in r.stdout.strip().splitlines():
        parts = line.split(" ", 2)
        if len(parts) != 3:
            continue
        sha, _ct, raw_subj = parts
        msg = raw_subj.split(": ", 1)[1] if ": " in raw_subj else raw_subj
        out.append(StashRef(sha=sha, message=msg))
    return out


def _recent_orphan_for_round(repo: Path, round_num: int, window_s: int) -> StashRef | None:
    r = subprocess.run(
        ["git", "stash", "list", "--format=%H %ct %s"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    line = r.stdout.strip().splitlines()[0]
    parts = line.split(" ", 2)
    if len(parts) != 3:
        return None
    sha, ct_s, raw_subj = parts
    try:
        ct = int(ct_s)
    except ValueError:
        return None
    msg = raw_subj.split(": ", 1)[1] if ": " in raw_subj else raw_subj
    if not msg.startswith(f"ORPHAN R{round_num}"):
        return None
    if (time.time() - ct) > window_s:
        return None
    return StashRef(sha=sha, message=msg)


def stash_orphan(
    repo: Path,
    *,
    round_num: int,
    phase: str | None,
    idempotency_s: int = 5,
) -> StashRef | None:
    """Stash dirty tree as ORPHAN entry, SHA-locked.

    Returns existing ref if a matching ORPHAN was created within ``idempotency_s``
    (R820 lesson — same-second multiple calls would otherwise pile up duplicate
    stashes). Returns None if tree is clean.
    """
    if not detect_dirty_files(repo):
        return None
    existing = _recent_orphan_for_round(repo, round_num, idempotency_s)
    if existing is not None:
        return existing
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    phase_part = f" phase={phase}" if phase else ""
    msg = f"ORPHAN R{round_num}{phase_part} ts={ts}"
    push = subprocess.run(
        ["git", "stash", "push", "-u", "-m", msg],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if push.returncode != 0:
        return None
    listing = subprocess.run(
        ["git", "stash", "list", "-1", "--format=%H %s"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if listing.returncode != 0 or not listing.stdout.strip():
        return None
    sha, _, raw_subj = listing.stdout.strip().partition(" ")
    if msg not in raw_subj:
        return None  # tree was clean — nothing to stash
    return StashRef(sha=sha, message=msg)


def _resolve_stash_selector(repo: Path, sha: str) -> str | None:
    """Resolve a stash commit SHA to its current reflog selector.

    Looked up immediately before each operation — callers never cache the
    selector (that would defeat the SHA-lock invariant under concurrent
    auto-stash).
    """
    r = subprocess.run(
        ["git", "stash", "list", "--format=%gd %H"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        sel, _, line_sha = line.partition(" ")
        if line_sha == sha:
            return sel
    return None


def drop_stash(repo: Path, sha: str) -> bool:
    """Drop stash by SHA — IMMUTABLE under concurrent stash (§9 lesson).

    SHA is resolved to its current reflog selector at call time; callers
    never persist or pass index references.
    """
    sel = _resolve_stash_selector(repo, sha)
    if sel is None:
        return False
    r = subprocess.run(
        ["git", "stash", "drop", sel],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode == 0


def pop_stash(repo: Path, sha: str) -> bool:
    """Pop stash by SHA. Same SHA-lock rule as drop_stash."""
    sel = _resolve_stash_selector(repo, sha)
    if sel is None:
        return False
    r = subprocess.run(
        ["git", "stash", "pop", sel],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode == 0
