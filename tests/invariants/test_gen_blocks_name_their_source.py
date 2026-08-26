"""Render must prepend a `<!-- source: … -->` provenance line to every generated
block, naming the code SSOT at the point a reader would otherwise edit prose.

test_docs_generated pins disk == fresh-render, but that alone cannot guard the
prepend: drop it and both sides move together, staying green while provenance
silently vanishes. So this asserts the prepend at the CODE level, against a tmp
doc, independent of committed output — and reads the expected source off the
paired `Renderer`, so the two can never disagree.
"""

from __future__ import annotations

from pathlib import Path

from agent_runner._docgen import RENDERERS, render


def test_render_prepends_the_declared_source_line(tmp_path: Path) -> None:
    name = "event-kinds"  # representative static renderer
    source = RENDERERS[name].source
    doc = tmp_path / "sample.md"
    doc.write_text(f"before\n<!-- gen:{name} -->\n<!-- /gen:{name} -->\nafter\n", encoding="utf-8")

    render(docs_dir=tmp_path, write=True)

    rendered = doc.read_text(encoding="utf-8")
    inner = rendered.split(f"<!-- gen:{name} -->\n", 1)[1]
    assert inner.startswith(f"<!-- source: {source} -->\n"), rendered
