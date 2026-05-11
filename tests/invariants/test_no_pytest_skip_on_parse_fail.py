"""anti-pattern Class 8: invariant tests must NEVER use pytest.skip() as
a fallback for subprocess parse failures or unexpected returncodes.

Use pytest.fail() instead — silent skip = false-green = bug ships to prod.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent / "invariants"


def test_given_invariant_test_files_when_scanned_then_no_pytest_skip_on_parse_fail() -> None:
    offenders: list[tuple[str, int]] = []
    for f in TESTS.glob("test_*.py"):
        if f.name == "test_no_pytest_skip_on_parse_fail.py":
            continue
        text = f.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "skip"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "pytest"
                ):
                    # check if it's inside an except block (likely error fallback)
                    # — for MVP we are strict: any pytest.skip in invariants/ is suspect
                    offenders.append((f.name, node.lineno))
    assert offenders == [], (
        f"pytest.skip in tests/invariants/ — use pytest.fail instead: {offenders}"
    )
