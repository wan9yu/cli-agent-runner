"""Git operations — ONLY module that calls git CLI.

Stash safety rules (R820 + §9 IMMUTABLE):
- API is SHA-locked: a stash is identified by the SHA in ``StashRef``, never
  by a stash@{N} index — an index shifts when any stash is pushed or dropped.
- Unified-diff +/-line parsing is forbidden (R2110 lesson): git's diff aligner
  emits cosmetic +/- markers on moved sections, so a +/-scan misclassifies both
  ways. Any auto-tool-vs-human classification must compare line sets vs HEAD.
  Enforced by tests/invariants/test_set_diff_for_auto_tool_classification.py.
- Also hosts the plugin-owned-paths registry, honored by ``detect_dirty_files()``
  (not reported as orphan WIP) and by ``stash_orphan()`` (excluded from the
  stash pathspec) so plugins can opt files/dirs out of the orphan-stash defense.
"""

from __future__ import annotations

import fnmatch
import subprocess  # noqa: TID251 — vcs_state.py is the only sanctioned git CLI caller
import time
from dataclasses import dataclass, replace
from pathlib import Path, PurePath

# Plugin-owned paths registry — set via register_plugin_owned_paths().
# Two consumers honor it: detect_dirty_files() filters its return (not flagged as
# orphan WIP) and _owned_exclude_specs() feeds stash_orphan()'s pathspec (not swept
# off disk by ``git stash push -u``). They scan differently -- see _owned_exclude_specs.
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
            # fnmatch handles ** recursively; PurePath.match (3.11) does not.
            if fnmatch.fnmatch(path, pattern):
                return True
        elif PurePath(path).match(pattern):
            return True
    return False


class AutoCommitError(RuntimeError):
    """git add/commit failed during try_auto_commit (reason capped at 200 chars)."""


class StashError(RuntimeError):
    """git stash push failed during stash_orphan (reason capped at 200 chars)."""


@dataclass(frozen=True)
class StashRef:
    sha: str  # full commit SHA — IMMUTABLE under concurrent stash
    message: str  # human-readable label set at creation
    reused: bool = False  # True only when stash_orphan returned an idempotency-window hit


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
    """
    return subprocess.run(
        ["git", *pre_flags, *args],
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


def _porcelain_paths(repo: Path, *, untracked_all: bool = False) -> list[str]:
    """Every path with an uncommitted change, before owned-path filtering.

    Uses ``git status --porcelain -z`` (NUL-separated, rename pairs split into
    two records). Returns the new-path side of any rename; old paths are skipped.

    ``untracked_all`` adds ``-uall`` so a wholly-untracked directory is listed as
    its individual files rather than collapsed to a single ``dir/`` entry. Only
    ``_owned_exclude_specs`` needs that; ``detect_dirty_files`` keeps the default
    ``-unormal`` because its output is user-visible (the ``orphan_stashed`` event
    and ``orphan-state.json``) and must not change shape for existing users.
    """
    args = ["status", "--porcelain", "-z"]
    if untracked_all:
        args.append("-uall")
    r = _git(repo, *args)
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
    """Files with any uncommitted change, minus paths claimed by the owned-paths registry."""
    out = _porcelain_paths(repo)
    # Early-out preserves zero behavior change when no plugin has registered.
    if _PLUGIN_OWNED_PATHS:
        out = [p for p in out if not _matches_owned_path(p)]
    return out


def _owned_exclude_specs(repo: Path) -> list[str]:
    """``:(exclude)`` pathspecs for every dirty path the owned-paths registry claims.

    Built from concrete dirty paths run through ``_matches_owned_path`` rather than by
    translating registered patterns into pathspec syntax: bare-glob patterns match via
    ``PurePath.match``, which is right-anchored (``"reports/*.md"`` also matches
    ``sub/reports/dev.md``) while git pathspecs anchor at the repo root, so no faithful
    translation exists. Reusing the matcher is what keeps the git boundary honoring the
    same claims as the report boundary -- the invariant whose absence caused the sweep.

    Scans with ``-uall``: ``-unormal`` collapses a wholly-untracked directory to a
    single ``reports/`` entry, which the glob and ``**`` forms do not match, so a
    first-round deliverable would be swept despite being registered. Only the prefix
    form (``"proposals/"``) survives a collapsed entry.

    Consequence, accepted: the two boundaries no longer emit identical path *lists* --
    a report may name ``reports/`` where the excludes name ``reports/dev.md``. They
    agree on what actually reaches the stash, which is the guarantee that matters. The
    residue is that ``orphan_stashed`` can still name a collapsed dir whose owned
    contents were not in fact stashed; that is the pre-existing reporting imprecision
    of collapsed entries, not something this scan introduced.

    Ignore-matched paths are skipped: naming one in a stash pathspec makes
    ``git stash push -u`` return rc=1 (the 0.1.42 lesson). ``--no-index`` so a
    tracked file under an ignored directory is caught too -- plain ``check-ignore``
    reports rc=1 (not ignored) for that shape yet the push still trips. ``--`` so a
    leading-dash path (``-out/memo.md``) is read as a pathname, not a switch: git
    would exit 129, which reads as "not ignored" and lands the path in the pathspec.
    """
    if not _PLUGIN_OWNED_PATHS:
        return []
    out: list[str] = []
    for p in _porcelain_paths(repo, untracked_all=True):
        if not _matches_owned_path(p):
            continue
        if _git(repo, "check-ignore", "-q", "--no-index", "--", p).returncode == 0:
            continue
        out.append(f":(exclude){p}")
    return out


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
    if (time.time() - ct) > window_s:
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
      round that churned only ``log_dir`` / plugin-owned paths) — whether the stash
      stack is empty or an older unrelated stash sits on top;
    - KNOWN GAP: the push succeeded but the follow-up ``git stash list`` failed, so
      the WIP *is* stashed and only its ref was lost — callers still read "nothing
      stashed" and report the tree as ignored. No event kind carries that meaning
      (``orphan_stash_failed`` would be wrong: the stash exists), and naming it is a
      design decision rather than cleanup. Left as-is: a listing that fails
      microseconds after a push that just succeeded in the same repo is effectively
      unreachable.

    Raises StashError when ``git stash push`` itself fails — e.g. intent-to-add
    index entries ("Entry '<f>' not uptodate. Cannot merge."). Callers must not
    read that as a clean tree: the WIP is still on disk.

    ``log_dir`` (when under ``repo``) and every dirty plugin-owned path are
    excluded so ``git stash push -u`` sweeps neither the runner's own bookkeeping
    (lock / pid / event logs) nor the plugin's deliverables out of the work tree.
    """
    if not detect_dirty_files(repo):
        return None
    existing = _recent_orphan_for_round(repo, round_num, idempotency_s)
    if existing is not None:
        return replace(existing, reused=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    phase_part = f" phase={phase}" if phase else ""
    msg = f"ORPHAN R{round_num}{phase_part} ts={ts}"
    exclude = _log_dir_exclude_pathspec(repo, log_dir)
    owned = _owned_exclude_specs(repo)
    if owned:
        # _log_dir_exclude_pathspec already opens the pathspec with "--" when non-empty.
        exclude = [*exclude, *owned] if exclude else ["--", *owned]
    push = _git(repo, "stash", "push", "-u", "-m", msg, *exclude, timeout=30)
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
    Subject: ``agent-runner auto-commit: R<N> <phase>``. Uses
    ``git -c commit.gpgsign=false``; honors pre-commit hooks (no --no-verify).
    """
    phase_part = f" {phase}" if phase else ""
    subject = f"agent-runner auto-commit: R{round_num}{phase_part}"

    exclude = _log_dir_exclude_pathspec(work_dir, log_dir)
    add_result = _git(work_dir, "add", "-A", *exclude)
    if add_result.returncode != 0:
        raise AutoCommitError((add_result.stderr or "git add failed")[:200])

    # Only the exclusion can leave nothing staged (a zero-work round that churned
    # only log_dir); without it the tree was dirty so there is always something to
    # commit. Skip the extra git call on the common (no-exclusion) path.
    if exclude and _git(work_dir, "diff", "--cached", "--quiet").returncode == 0:
        return ""

    commit_result = _git(
        work_dir,
        "commit",
        "-m",
        subject,
        pre_flags=("-c", "commit.gpgsign=false"),
    )
    if commit_result.returncode != 0:
        raise AutoCommitError((commit_result.stderr or "git commit failed")[:200])

    head = _git(work_dir, "rev-parse", "HEAD")
    return head.stdout.strip()
