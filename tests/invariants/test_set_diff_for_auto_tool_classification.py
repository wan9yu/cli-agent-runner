"""R2110 — classification must never scan unified-diff +/- lines.

git's diff aligner emits cosmetic +/- markers when sections move, so a +/-line
scan produces both false positives (a real edit read as automated) and false
negatives (a repeated heading read as a user edit). Comparing line *sets*
against HEAD ignores all alignment noise. README.md states the rule as a
prohibition ("line-set comparison, not unified-diff +/- scan"); this scan
enforces it as one.

Precision: a bare ``startswith("-")`` is not a violation — ssh host validation
in monitor.py and the frontmatter check in prompt_loader.py legitimately use
it, and no +/- classifier works without also inspecting "+".
"""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"

_RE_MODULES = {"re", "regex"}
_PREFIX_METHODS = {"startswith", "removeprefix"}
# Anchored char classes and a literal escaped plus — the shapes a hunk-line
# regex takes. Bare "-" is deliberately absent (see module docstring).
_DIFF_REGEX_FRAGMENTS = ("[+-]", "[-+]", "^\\+")


def _str_constants(node: ast.AST) -> list[str]:
    """String constants reachable as ``node`` itself or as its tuple/list/set elements."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        out: list[str] = []
        for elt in node.elts:
            out.extend(_str_constants(elt))
        return out
    return []


def _is_diff_marker_set(values: list[str]) -> bool:
    """True for "+" alone, or any set carrying both a +-prefixed and a --prefixed marker."""
    if "+" in values:
        return True
    return any(v.startswith("+") for v in values) and any(v.startswith("-") for v in values)


def _violations(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            if node.func.attr in _PREFIX_METHODS:
                values = _str_constants(node.args[0])
                if values and _is_diff_marker_set(values):
                    found.append(f"line {node.lineno}: .{node.func.attr}({values!r})")
            if isinstance(node.func.value, ast.Name) and node.func.value.id in _RE_MODULES:
                for pat in _str_constants(node.args[0]):
                    if any(frag in pat for frag in _DIFF_REGEX_FRAGMENTS):
                        found.append(f"line {node.lineno}: re.{node.func.attr}({pat!r})")
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Subscript):
            index = node.left.slice
            if isinstance(index, ast.Constant) and index.value == 0:
                values = []
                for comparator in node.comparators:
                    values.extend(_str_constants(comparator))
                if values and _is_diff_marker_set(values):
                    found.append(f"line {node.lineno}: [0] compared against {values!r}")
    return found


def test_given_production_modules_when_scanned_then_no_unified_diff_marker_parsing() -> None:
    failures: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        hits = _violations(ast.parse(path.read_text(encoding="utf-8")))
        failures.extend(f"{path.relative_to(PKG.parent)}: {h}" for h in hits)
    assert not failures, "unified-diff +/- line parsing is forbidden (R2110):\n" + "\n".join(
        failures
    )


def test_given_diff_scanning_shapes_when_checked_then_scan_flags_them() -> None:
    """The scan's own teeth — each shape is a way R2110 has been reintroduced."""
    for src in (
        'if line.startswith("+"): pass',
        'if line.startswith(("+", "-")): pass',
        'if line.startswith(("+++", "---")): pass',
        'if line[0] == "+": pass',
        'if line[0] in ("+", "-"): pass',
        'import re\nre.match(r"^[+-]", line)',
    ):
        assert _violations(ast.parse(src)), f"scan missed diff-marker parsing: {src!r}"
    for src in (
        'if host.startswith("-"): pass',
        'if text.startswith("---\\n"): pass',
        'if status[0] == "R": pass',
    ):
        assert not _violations(ast.parse(src)), f"scan false-positives on: {src!r}"
