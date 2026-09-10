"""BDD test conventions, locked so the repo-wide carpet-verification convention
cannot rot:

- every test is named ``subject_should_outcome_when_condition`` (``_should_`` is
  the load-bearing marker; ``_when_`` is present only when the behavior is
  conditional);
- test bodies are grouped into blank-line-separated given/when/then sections
  with NO section-label comments -- the blank lines alone carry the structure.

Prevent > detect: without these gates a later test drifts back to a bare
``test_thing`` name or a wall-of-code body, and the convention erodes silently.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
_SELF = Path(__file__).name


def _test_functions() -> list[tuple[Path, ast.FunctionDef, str]]:
    out: list[tuple[Path, ast.FunctionDef, str]] = []
    for f in TESTS.rglob("test_*.py"):
        if "__pycache__" in str(f) or f.name == _SELF:
            continue
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                out.append((f, node, src))
    return out


def test_every_test_should_carry_should_in_its_name() -> None:
    functions = _test_functions()

    offenders = [
        f"{f.relative_to(TESTS.parent)}::{node.name}"
        for f, node, _ in functions
        if "_should_" not in node.name
    ]

    assert functions, "vacuity guard: no test functions scanned"
    assert not offenders, (
        "tests must be named subject_should_outcome_when_condition (BDD carpet "
        f"convention); {len(offenders)} lack `_should_`:\n" + "\n".join(sorted(offenders))
    )


# A STANDALONE section-label comment only: the label word optionally with a
# trailing colon, and nothing else on the comment. This catches `# given`,
# `# Then:`, `result = x  # act` -- but NOT ordinary rationale prose that merely
# starts with one of these words (`# given the file is large, we...`), which is a
# legitimate comment, not a section marker.
_SECTION_LABEL = re.compile(r"#\s*(given|when|then|arrange|act|assert)\s*:?\s*$", re.IGNORECASE)


def test_test_bodies_should_omit_section_label_comments() -> None:
    # Scan the whole function span (node.lineno..end_lineno), not from the first
    # BODY statement -- a `# given` sitting between the signature and the first
    # statement (or before a docstring) must not slip through.
    offenders: list[str] = []
    for f, node, src in _test_functions():
        lines = src.splitlines()
        for i in range(node.lineno - 1, node.end_lineno):
            if _SECTION_LABEL.search(lines[i]):
                offenders.append(f"{f.relative_to(TESTS.parent)}:{i + 1}: {lines[i].strip()}")

    assert not offenders, (
        "test bodies must use blank-line grouping, not section-label comments "
        "(no standalone `# given` / `# when` / `# then` / `# arrange` / `# act` / `# assert`):\n"
        + "\n".join(offenders)
    )


def _body_statements(node: ast.FunctionDef) -> list[ast.stmt]:
    """Top-level statements of the test body, minus a leading docstring. A single
    multi-line literal, or one for/with/if block, is ONE statement here -- so line
    count never inflates a cohesive test into a false 'wall'."""
    stmts = node.body
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]
    return stmts


def test_nontrivial_test_bodies_should_be_grouped_with_blank_lines() -> None:
    # Count top-level STATEMENTS, not source lines: a 12-line single literal is 1
    # statement (never a wall), while 6+ sequential statements with NO interior
    # blank line is a genuine ungrouped wall -- the given/when/then structure wants
    # blank-line groups. A leading/trailing blank doesn't count; only a blank
    # BETWEEN the first and last statement proves grouping.
    offenders: list[str] = []
    for f, node, src in _test_functions():
        stmts = _body_statements(node)
        if len(stmts) < 6:
            continue  # short/cohesive bodies need no grouping

        lines = src.splitlines()
        interior = lines[stmts[0].lineno : stmts[-1].end_lineno - 1]
        if not any(not ln.strip() for ln in interior):
            offenders.append(
                f"{f.relative_to(TESTS.parent)}::{node.name} ({len(stmts)} stmts, ungrouped)"
            )

    assert not offenders, (
        "non-trivial test bodies (>=6 statements) must be grouped into "
        "given/when/then sections separated by a blank line:\n" + "\n".join(sorted(offenders))
    )
