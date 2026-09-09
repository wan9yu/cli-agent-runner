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


_SECTION_LABEL = re.compile(r"#\s*(given|when|then|arrange|act|assert)\b", re.IGNORECASE)


def test_test_bodies_should_omit_section_label_comments() -> None:
    offenders: list[str] = []
    for f, node, src in _test_functions():
        lines = src.splitlines()
        for i in range(node.body[0].lineno - 1, node.end_lineno):
            if _SECTION_LABEL.search(lines[i]):
                offenders.append(f"{f.relative_to(TESTS.parent)}:{i + 1}: {lines[i].strip()}")

    assert not offenders, (
        "test bodies must use blank-line grouping, not section-label comments "
        "(no `# given` / `# when` / `# then` / `# arrange` / `# act` / `# assert`):\n"
        + "\n".join(offenders)
    )


def _body_start_line(node: ast.FunctionDef) -> int:
    first = node.body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first.end_lineno + 1  # skip a leading docstring
    return node.body[0].lineno


def test_nontrivial_test_bodies_should_be_grouped_with_blank_lines() -> None:
    # A body of >=10 code lines with zero blank-line grouping is a wall; the
    # convention wants given/when/then separated by blank lines. The threshold
    # stays generous so short tests and single cohesive loops (a for-loop with an
    # inline assert has no natural blank point) never trip -- it targets genuine
    # ungrouped walls, not every multi-line test.
    offenders: list[str] = []
    for f, node, src in _test_functions():
        lines = src.splitlines()
        span = lines[_body_start_line(node) - 1 : node.end_lineno]
        code = [ln for ln in span if ln.strip() and not ln.strip().startswith("#")]
        blanks = [ln for ln in span if not ln.strip()]
        if len(code) >= 10 and not blanks:
            offenders.append(
                f"{f.relative_to(TESTS.parent)}::{node.name} ({len(code)} code lines, 0 blanks)"
            )

    assert not offenders, (
        "non-trivial test bodies (>=10 code lines) must be grouped into "
        "given/when/then sections separated by blank lines:\n" + "\n".join(sorted(offenders))
    )
