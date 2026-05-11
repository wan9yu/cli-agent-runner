"""All persistent state writes must go through atomic_write_json (tmp + rename)."""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


def test_given_context_store_writers_when_scanned_then_use_atomic_helper() -> None:
    text = (PKG / "context_store.py").read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("write_"):
            calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
            call_names = []
            for c in calls:
                if isinstance(c.func, ast.Name):
                    call_names.append(c.func.id)
                elif isinstance(c.func, ast.Attribute):
                    call_names.append(c.func.attr)
            assert "atomic_write_json" in call_names, (
                f"{node.name} must call atomic_write_json — found calls: {call_names}"
            )
