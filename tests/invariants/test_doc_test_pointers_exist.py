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
    for doc in doc_files():
        for m in _PTR.finditer(doc.read_text(encoding="utf-8")):
            path = ROOT / m.group(1)
            if not path.is_file():
                missing.append(f"{doc.relative_to(ROOT)} → {m.group(1)} (does not exist)")
    assert not missing, "doc pointers to non-existent tests:\n" + "\n".join(missing)
