"""Every generated doc region must name its code SSOT on the first line, so a reader
learns where the fact lives at the point they'd otherwise edit prose."""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = sorted((_ROOT / "docs").glob("*.md")) + sorted((_ROOT / "docs/recipes").glob("*.md"))
_BLOCK = re.compile(r"<!-- gen:([a-z0-9-]+) -->\n(.*?)<!-- /gen:\1 -->", re.DOTALL)


def test_every_gen_block_first_line_is_a_source_comment():
    offenders = []
    for doc in _DOCS:
        for m in _BLOCK.finditer(doc.read_text(encoding="utf-8")):
            first = m.group(2).splitlines()[0] if m.group(2).strip() else ""
            if not first.startswith("<!-- source:"):
                rel = doc.relative_to(_ROOT)
                offenders.append(f"{rel}: gen:{m.group(1)} — first line not a source comment")
    assert not offenders, "generated blocks missing provenance:\n" + "\n".join(offenders)
