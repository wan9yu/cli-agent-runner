"""§9 IMMUTABLE — vcs_state must never name a stash by stash@{N} index.

R820 + orphan-stash-archive-2026-04-23 lesson: concurrent auto-stash shifts
indices, so an index captured at one moment names a different stash at the
next. Stashes are identified by the SHA that stash_orphan returns.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"

# A stash named by index, in either source form:
#   literal    "stash@{0}"        → stash@{ then a digit
#   f-string   f"stash@{{{idx}}}" → ast.unparse gives stash@{{{ (escaped brace + expr)
_INDEX_REF = re.compile(r"stash@\{(?:\{|[0-9])")


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
    """id()s of the Constant nodes that are module/func/class docstrings — the module
    docstring here literally says ``stash@{N}`` to explain the ban, so a raw-text
    regex false-positives; excluding docstrings scopes the scan to real code."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_given_vcs_state_stash_calls_when_scanned_then_no_stash_at_brace_index() -> None:
    """§9 IMMUTABLE — forbid naming a stash by ``stash@{N}`` index (literal or the
    ``f"stash@{{{idx}}}"`` interpolated form) anywhere in real code."""
    tree = ast.parse((PKG / "vcs_state.py").read_text())
    docstrings = _docstring_constant_ids(tree)
    scanned = 0
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            scanned += 1
            if _INDEX_REF.search(node.value):
                offenders.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            scanned += 1
            if _INDEX_REF.search(ast.unparse(node)):
                offenders.append(ast.unparse(node))
    assert scanned > 0, "no string literals scanned in vcs_state.py"  # vacuity-guard
    assert offenders == [], f"vcs_state.py names a stash by index: {offenders}"
