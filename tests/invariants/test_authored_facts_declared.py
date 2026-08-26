# tests/invariants/test_authored_facts_declared.py
"""The ratchet: prose may not AUTHOR a config default — that fact lives in code and
renders via gen:config-schema. A legitimate default mentioned in narrative must be
DECLARED with `<!-- authored: <reason> -->` so it's a reviewed choice, not silent drift.

Scope (v1, deliberately narrow, high-signal): a known config field name appearing on a
prose line together with a default literal, OUTSIDE fenced code blocks and generated
regions. Config examples live in ```toml blocks (excluded); generated schema lives in
gen: regions (excluded). Extend with more checks (flags, enum value-sets) later.

Known v1 limitation: the check is line-based, so a sentence whose field name and
"default" wrap onto different lines is not caught (a silent-PASS, never a silent-fire).
A v2 could scan a small line-window; v1 accepts the gap deliberately."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from agent_runner import config

_ROOT = Path(__file__).resolve().parents[2]
# Scope: docs/*.md + docs/recipes/*.md. Intentionally NOT scanned: docs/migrations/
# (legitimately states old defaults as history) and repo-root README.md (no default
# literals today). Extend the glob only with a matching scope decision.
_DOCS = sorted((_ROOT / "docs").glob("*.md")) + sorted((_ROOT / "docs/recipes").glob("*.md"))
_AUTHORED = re.compile(r"<!--\s*authored:")
# The cue is the WORD "default"/"defaults" (case-insensitive — catches "Default behavior").
# Do NOT add an `= <literal>` alternative: it matches inline TOML enum snippets
# (dirty_action = "stash") that state no default → false positives (measured: 6).
_DEFAULT_CUE = re.compile(r"\bdefaults?\b", re.IGNORECASE)


def _blank_preserving_sub(pat: re.Pattern, text: str) -> str:
    # Replace each match with the SAME number of newlines it contained, so line
    # numbers after a fenced/generated region stay correct in failure messages.
    return pat.sub(lambda m: "\n" * m.group(0).count("\n"), text)


_FENCE = re.compile(r"```.*?```", re.DOTALL)
_GEN = re.compile(r"<!-- gen:[a-z0-9-]+ -->.*?<!-- /gen:[a-z0-9-]+ -->", re.DOTALL)


def _config_field_names() -> set[str]:
    names: set[str] = set()
    for obj in vars(config).values():
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            names.update(f.name for f in dataclasses.fields(obj))
    # Drop names too generic to be safe signals (avoid false positives on plain English).
    # Verified: max_rounds / stop_file survive; only generic words (dry_run, log_dir,
    # timezone, env, file) drop — none currently anchor an uncaught prose default.
    return {n for n in names if len(n) >= 8 and "_" in n}


def _prose_lines(text: str) -> list[tuple[int, str]]:
    stripped = _blank_preserving_sub(_GEN, _blank_preserving_sub(_FENCE, text))
    return [(i, ln) for i, ln in enumerate(stripped.splitlines(), 1)]


def test_no_undeclared_config_default_in_prose():
    fields = _config_field_names()
    offenders: list[str] = []
    for doc in _DOCS:
        lines = _prose_lines(doc.read_text(encoding="utf-8"))
        for idx, (lineno, line) in enumerate(lines):
            prev = lines[idx - 1][1] if idx > 0 else ""
            # Escape hatch: `<!-- authored: reason -->` on the same OR previous line
            # (previous line for narrative sentences; SAME line, in-cell, for GFM table
            # rows — an above-line comment would split the table).
            if _AUTHORED.search(line) or _AUTHORED.search(prev):
                continue
            if any(f in line for f in fields) and _DEFAULT_CUE.search(line):
                offenders.append(f"{doc.relative_to(_ROOT)}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        "Undeclared config-default fact(s) in prose — move to code (gen:config-schema), "
        "delete the duplicate, or declare with `<!-- authored: reason -->`:\n"
        + "\n".join(offenders)
    )
