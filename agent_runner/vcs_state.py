"""Git operations — ONLY module that calls git CLI.

Stash safety rules (R820 + §9 IMMUTABLE):
- All stash refs locked by SHA, not stash@{N} index (race-safe under concurrent stash).
- "Auto-tool change vs human change" detection uses set-based diff vs HEAD,
  not unified-diff +/-line parsing (R2110 lesson).
"""

from __future__ import annotations

import subprocess  # noqa: TID251 — vcs_state.py is the only sanctioned git CLI caller
from pathlib import Path


def is_git_repo(path: Path) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def detect_dirty_files(repo: Path) -> list[str]:
    """Return list of files with any uncommitted change (modified / untracked)."""
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return []
    out: list[str] = []
    for line in r.stdout.splitlines():
        if len(line) > 3:
            out.append(line[3:].strip())
    return out


def set_diff_vs_head(repo: Path, path: Path) -> set[str]:
    """Lines present in working-tree path but absent from HEAD:path.

    Uses set comparison, NOT unified-diff +/-line parsing. R2110 lesson:
    git's diff aligner emits cosmetic +/- markers when sections move, so
    +/-line scanning produces both false positives (mis-classifies real
    edits as automated) and false negatives (mis-classifies repeated
    headings as user edits). Set comparison ignores all alignment noise.
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
    wt_lines = set(wt_path.read_text().splitlines())
    return wt_lines - head_lines
