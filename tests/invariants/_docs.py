"""Shared doc-file glob for the doc invariants.

Top-level ``docs/*.md`` plus ``docs/recipes/*.md`` — the same set
``_docgen.render()`` walks. Deliberately NOT ``rglob``: ``docs/migrations/`` is
frozen history and ``docs/internal/`` is gitignored. Kept in one place so a
scope change lands in a single edit, not once per invariant.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def doc_files() -> list[Path]:
    """Docs the invariants scan, in a stable order."""
    return sorted((ROOT / "docs").glob("*.md")) + sorted((ROOT / "docs/recipes").glob("*.md"))
