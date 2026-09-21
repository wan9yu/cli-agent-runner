"""BDD body shape that ratch ``blank_blocks=0`` does not cover.

Names, ``or`` segments, and standalone ``# given`` / ``# when`` labels are
ratch ``BddTestConventions``. This file keeps bodies with >=6 statements
grouped by a blank line.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._test_helpers import ROOT

TESTS = ROOT / "tests"
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


def test_nontrivial_test_bodies_should_be_grouped_with_blank_lines_when_invoked() -> None:
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
        end_lineno = stmts[-1].end_lineno or stmts[-1].lineno
        interior = lines[stmts[0].lineno : end_lineno - 1]
        if not any(not ln.strip() for ln in interior):
            offenders.append(
                f"{f.relative_to(TESTS.parent)}::{node.name} ({len(stmts)} stmts, ungrouped)"
            )

    assert not offenders, (
        "non-trivial test bodies (>=6 statements) must be grouped into "
        "given/when/then sections separated by a blank line:\n" + "\n".join(sorted(offenders))
    )
