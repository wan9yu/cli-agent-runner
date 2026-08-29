# tests/invariants/test_doc_test_pointers_exist.py
"""A doc that points at a test as its runnable proof must point at a test that EXISTS —
so 'see tests/…::test_…' can't rot into a dangling reference."""

import re

from tests.invariants._docs import ROOT, doc_files

# Backtick-quoted paths under tests/, optionally with a ::test_name suffix.
# NB: 13 of these matches sit inside the generated `gen:defenses-table` guarded_by
# column — harmless double coverage with test_catalogs' own existence check.
_PTR = re.compile(r"`(tests/[A-Za-z0-9_./-]+\.py)(::[A-Za-z0-9_]+)?`")


def test_every_doc_test_pointer_resolves():
    missing = []
    checked = 0
    for doc in doc_files():
        for m in _PTR.finditer(doc.read_text(encoding="utf-8")):
            checked += 1
            path = ROOT / m.group(1)
            if not path.is_file():
                missing.append(f"{doc.relative_to(ROOT)} → {m.group(1)} (file does not exist)")
                continue
            # A `::test_name` suffix must name a def that EXISTS — a renamed test would
            # otherwise leave the path valid but the proof dangling.
            if m.group(2):
                name = m.group(2)[2:]  # strip "::"
                src = path.read_text(encoding="utf-8")
                if not re.search(rf"def\s+{re.escape(name)}\s*\(", src):
                    missing.append(
                        f"{doc.relative_to(ROOT)} → {m.group(1)}{m.group(2)} (no such def)"
                    )
    assert checked > 0, "no doc test pointers scanned — pattern or corpus broke"  # vacuity-guard
    assert not missing, "doc pointers to non-existent tests:\n" + "\n".join(missing)
