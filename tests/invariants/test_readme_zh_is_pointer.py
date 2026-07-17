"""Invariant: README.zh.md stays a pointer, not a second README.

The Chinese README was a full fork of README.md and drifted in both
directions — counts, a defense the English README never claimed, a link into
the gitignored docs/internal/ tree, a "230+ tests" figure. A pointer has
nothing to drift. The four count claims this file used to carry were guarded
by test_doc_claims_match_ssot's registry until 0.2.2; those entries are gone,
so reintroducing a count here would be unguarded by construction.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ZH = REPO / "README.zh.md"

# Line budget, not a target: a pointer plus install prose plus a link list.
MAX_LINES = 40

# The exact patterns test_doc_claims_match_ssot.py's registry used to guard
# for this file. Re-adding any of them reintroduces unguarded count drift.
_RETIRED_COUNT_PATTERNS = (
    r"\d+ 个检测器",
    r"\d+ 条防御",
    r"（\d+ 条）",
    r"\d+ 个动词",
)


def test_given_readme_zh_when_measured_then_stays_a_thin_pointer() -> None:
    text = ZH.read_text(encoding="utf-8")
    lines = len(text.splitlines())
    assert lines <= MAX_LINES, (
        f"README.zh.md is {lines} lines (max {MAX_LINES}) — it must stay a "
        f"pointer to the English docs, not a fork of README.md"
    )
    assert "](README.md)" in text, "README.zh.md must link the English README"
    assert "](docs/architecture.md)" in text, "README.zh.md must link docs/architecture.md"


def test_given_readme_zh_when_scanned_then_carries_no_unguarded_counts() -> None:
    text = ZH.read_text(encoding="utf-8")
    failures = [p for p in _RETIRED_COUNT_PATTERNS if re.search(p, text)]
    assert not failures, (
        f"README.zh.md reintroduced unguarded count claims {failures} — state "
        f"counts in README.md (guarded) and link to it instead"
    )
    # docs/internal/ is gitignored (.gitignore:2): a link there is dead for
    # every reader who did not write it.
    assert "docs/internal/" not in text, (
        "README.zh.md links into the gitignored docs/internal/ tree"
    )
