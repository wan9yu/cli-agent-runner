"""Substrate fingerprint helpers — pure file/subprocess I/O, no events emit.

Separated from cli/serve_cmd.py for testability and to keep the supervisor
loop file lean. Used at round boundaries to capture git HEAD + optional
file-tree content hash; raw data for downstream confabulation detectors.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def compute_git_head(work_dir: Path) -> str | None:
    """Return SHA of work_dir HEAD, or None if not a git repo / git missing.

    Bounded to 5s timeout; never raises. Negligible cost (~5-10ms on healthy
    git repos). Called twice per round (before + after subprocess).
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(work_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    sha = r.stdout.strip()
    return sha or None


def compute_paths_hash(work_dir: Path, patterns: list[str]) -> str | None:
    """Sha256 of (sorted relative file_path + sha256-of-content) chain.

    Returns None if patterns empty. Globs evaluated against work_dir.
    Files that fail to read are silently skipped (don't fail the whole hash).
    Order: globs may match overlapping files; deduplicated by resolved path,
    then sorted for stable hash across rounds.
    """
    if not patterns:
        return None
    matched: list[Path] = []
    for pattern in patterns:
        matched.extend(work_dir.glob(pattern))
    unique_sorted = sorted({p.resolve() for p in matched if p.is_file()})
    h = hashlib.sha256()
    work_dir_resolved = work_dir.resolve()
    for p in unique_sorted:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        try:
            rel = p.relative_to(work_dir_resolved)
        except ValueError:
            rel = p
        h.update(str(rel).encode("utf-8"))
        h.update(b"\n")
        h.update(hashlib.sha256(content).hexdigest().encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()
