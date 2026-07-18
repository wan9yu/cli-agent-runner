"""Invariant: docs/README.md's reading order indexes every published page.

Five pages — including plugins.md and events.md, the two plugin-author
references — were orphans: reachable only by knowing the filename.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

# Not reader-facing: internal/ is gitignored, migrations/ is history reached
# from CHANGELOG, marketing/ is retired in this release.
_EXCLUDED_DIRS = {"internal", "migrations", "marketing"}


def _published_pages() -> set[str]:
    out: set[str] = set()
    for path in DOCS.rglob("*.md"):
        rel = path.relative_to(DOCS)
        if rel.parts[0] in _EXCLUDED_DIRS or rel.name == "README.md":
            continue
        out.add(rel.as_posix())
    return out


def test_given_docs_dir_when_indexed_then_every_page_listed() -> None:
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\(([\w./-]+\.md)\)", text))
    missing = _published_pages() - linked
    assert not missing, f"docs/README.md's index omits published pages: {sorted(missing)}"
