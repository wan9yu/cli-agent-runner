"""Tenet-3: the framework is price-blind. No module outside builtin_plugins/
reads cost_usd except _emit's verbatim pass-through, and no price/pricing/
per_token/usd_rate identifier or arithmetic-on-cost appears in core. Call-graph
form (AST), NOT a cost|price|window token grep (which false-hits run_windows)."""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"
_FORBIDDEN_IDENTS = {"pricing", "per_token", "usd_rate", "price_table", "price_per"}


def _core_modules():
    for p in sorted(PKG.rglob("*.py")):
        if p.relative_to(PKG).parts[0] == "builtin_plugins":
            continue
        yield p


def _reads_cost_usd(node: ast.expr) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "cost_usd":
            return True
        if (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.slice, ast.Constant)
            and sub.slice.value == "cost_usd"
        ):
            return True
    return False


def test_core_is_price_blind():
    ident_hits, arith_hits = [], []
    scanned = 0
    for path in _core_modules():
        scanned += 1
        rel = path.relative_to(PKG.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_IDENTS:
                ident_hits.append(f"{rel}:{node.lineno} {node.id}")
            if isinstance(node, (ast.BinOp, ast.Compare)) and _reads_cost_usd(node):
                arith_hits.append(f"{rel}:{node.lineno}")
    assert scanned > 20, "core scan found too few modules — vacuous"  # vacuity-guard
    assert not ident_hits, f"price identifiers in core: {ident_hits}"
    assert not arith_hits, f"arithmetic/comparison on cost_usd in core: {arith_hits}"
