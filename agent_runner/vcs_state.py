"""Git operations — ONLY module that calls git CLI.

Stash safety rules (R820 + §9 IMMUTABLE):
- API is SHA-locked: callers pass and store SHA only, never stash@{N} index.
  Internal drop/pop translate SHA -> current selector immediately before each
  git call. Safe against caller-side index drift; single-supervisor-per-repo
  design means external concurrent ``git stash push`` is not a defended scenario.
- "Auto-tool change vs human change" detection uses set-based diff vs HEAD,
  not unified-diff +/-line parsing (R2110 lesson).
- Also hosts the plugin-owned-paths registry consumed by
  ``detect_dirty_files()`` so plugins can opt files/dirs out of the
  orphan-stash defense (0.1.8+).
"""

from __future__ import annotations

import fnmatch
import subprocess  # noqa: TID251 — vcs_state.py is the only sanctioned git CLI caller
import time
from dataclasses import dataclass
from pathlib import Path, PurePath

# Plugin-owned paths registry — set via register_plugin_owned_paths().
# detect_dirty_files() filters its return through this list, so plugin-declared
# paths are not flagged as orphan WIP and not stashed by the supervisor.
_PLUGIN_OWNED_PATHS: list[str] = []


def register_plugin_owned_paths(paths: list[str]) -> None:
    """Register paths the plugin considers its own deliverables.

    Paths are relative to the work_dir. Matching:

      - Trailing ``/`` → prefix match (e.g. ``"proposals/"`` matches
        ``"proposals/dev-round1.md"`` and the bare directory name).
      - Anything else without ``**`` → ``pathlib.PurePath.match`` glob
        (e.g. ``"reports/*.md"``). Single ``*`` does not cross slashes.
      - Patterns containing ``**`` → ``fnmatch.fnmatch`` (e.g.
        ``"logs/plugins/**/*"``). ``**`` matches recursive directory
        segments. (``PurePath.full_match`` would handle this natively
        but requires Python 3.13+; this project's minimum is 3.11.)

    Plugins call this at module import time (entry_point side-effect) so the
    paths are known before the first round runs.

    Raises ValueError on non-string entries.
    """
    for p in paths:
        if not isinstance(p, str):
            raise ValueError(f"register_plugin_owned_paths: non-string entry {p!r}")
    _PLUGIN_OWNED_PATHS.extend(paths)


def plugin_owned_paths() -> list[str]:
    """Snapshot of registered plugin-owned paths (for peek visibility)."""
    return list(_PLUGIN_OWNED_PATHS)


def _matches_owned_path(path: str) -> bool:
    """True if `path` matches any registered plugin-owned pattern."""
    for pattern in _PLUGIN_OWNED_PATHS:
        if pattern.endswith("/"):
            stripped = pattern.rstrip("/")
            if path == stripped or path.startswith(pattern):
                return True
        elif "**" in pattern:
            # Recursive glob: fnmatch lets * span '/' so ** matches deep paths.
            # Python 3.11 PurePath.match does not honour ** recursive semantics
            # (3.13+ has full_match); fnmatch.fnmatch fills the gap.
            if fnmatch.fnmatch(path, pattern):
                return True
        elif PurePath(path).match(pattern):
            return True
    return False


@dataclass(frozen=True)
class StashRef:
    sha: str  # full commit SHA — IMMUTABLE under concurrent stash
    message: str  # human-readable label set at creation


def _git(repo: Path, *args: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Single sanctioned wrapper for git CLI invocations.

    Centralises cwd / capture / text / timeout so individual call sites stay
    one-liners and the noqa pragma lives in exactly one place.
    """
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def is_git_repo(path: Path) -> bool:
    if not path.is_dir():
        return False
    r = _git(path, "rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def detect_dirty_files(repo: Path) -> list[str]:
    """Return list of files with any uncommitted change (modified / untracked / renamed).

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
    # Filter out plugin-declared paths (0.1.8+). Early-out preserves zero
    # behavior change when no plugin has registered anything.
    if _PLUGIN_OWNED_PATHS:
        out = [p for p in out if not _matches_owned_path(p)]
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
    head = _git(repo, "show", f"HEAD:{path}")
    if head.returncode != 0:
        return set()
    try:
        wt_text = (repo / path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    head_lines = set(head.stdout.splitlines())
    wt_lines = set(wt_text.splitlines())
    return wt_lines - head_lines


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


def list_recent_stashes(repo: Path, limit: int | None = None) -> list[StashRef]:
    args = ["stash", "list", "--format=%H %ct %s"]
    if limit is not None:
        args.insert(2, f"-{limit}")
    r = _git(repo, *args)
    if r.returncode != 0:
        return []
    out: list[StashRef] = []
    for line in r.stdout.strip().splitlines():
        parsed = _parse_stash_line(line)
        if parsed is None:
            continue
        sha, _ct, msg = parsed
        out.append(StashRef(sha=sha, message=msg))
    return out


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
    push = _git(repo, "stash", "push", "-u", "-m", msg, timeout=30)
    if push.returncode != 0:
        return None
    listing = _git(repo, "stash", "list", "-1", "--format=%H %s")
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
    r = _git(repo, "stash", "list", "--format=%gd %H")
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
    return _git(repo, "stash", "drop", sel).returncode == 0


def pop_stash(repo: Path, sha: str) -> bool:
    """Pop stash by SHA. Same SHA-lock rule as drop_stash."""
    sel = _resolve_stash_selector(repo, sha)
    if sel is None:
        return False
    return _git(repo, "stash", "pop", sel).returncode == 0
