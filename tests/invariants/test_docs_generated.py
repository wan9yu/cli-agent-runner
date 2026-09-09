"""CI gate — docs/*.md must equal a fresh render. Run `./build.sh docs` to fix."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner._docgen import render

DOCS = Path(__file__).resolve().parent.parent.parent / "docs"


def test_docs_should_match_on_disk_when_rendered_in_memory() -> None:
    rendered = render(docs_dir=DOCS, write=False)
    # vacuity-guard
    assert rendered and (DOCS / "architecture.md") in rendered, "docgen rendered nothing"

    diffs: list[str] = []
    for path, want in rendered.items():
        got = path.read_text(encoding="utf-8")
        if got != want:
            diffs.append(path.name)
    if diffs:
        pytest.fail(f"docs out of date: {diffs}. Run `./build.sh docs` and commit.")
