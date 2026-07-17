"""AST helpers shared by the event-kind invariants.

One scanner, not one per test file — the non-recursive glob and the
alias-blind ``events.emit`` match that hid real emit sites lived in a
private copy of this scan.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


def package_modules() -> Iterator[Path]:
    """Every agent_runner module the invariants scan, minus events.py itself.

    ``rglob``, not ``glob`` — ``cli/`` and ``builtin_plugins/`` hold real emit
    sites and a non-recursive glob never reaches them.
    """
    for path in sorted(PKG.rglob("*.py")):
        if path.name == "events.py" and path.parent == PKG:
            continue
        yield path


def emit_kind_args(tree: ast.Module) -> list[ast.expr]:
    """The ``kind`` argument expression of every ``events.emit()`` call in ``tree``.

    Alias-aware: ``events.emit`` (runner.py), bare ``emit`` (_emit.py) and
    ``emit as emit_event`` (monitor.py) all resolve here.
    """
    aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "agent_runner.events"
        for alias in node.names
        if alias.name == "emit"
    }
    args: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        func = node.func
        qualified = (
            isinstance(func, ast.Attribute)
            and func.attr == "emit"
            and isinstance(func.value, ast.Name)
            and func.value.id == "events"
        )
        if qualified or (isinstance(func, ast.Name) and func.id in aliases):
            args.append(node.args[1])
    return args


def kind_literals(arg: ast.expr) -> list[ast.Constant]:
    """Every string literal inside a kind argument expression.

    Walks the expression instead of type-checking its root: a kind can be a
    ternary (``A if cond else B``), and a root-only isinstance sees neither branch.
    """
    return [
        node
        for node in ast.walk(arg)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def kind_constant_names(arg: ast.expr) -> set[str]:
    """Constant names that a kind argument can EVALUATE TO.

    default_dirty_handler picks its kind with a ternary
    (``events.ORPHAN_IDEMPOTENT_SKIP if ref.reused else events.ORPHAN_STASHED``),
    so this descends into value positions — the branches of an ``if``/``or`` — and
    returns each branch's constant name. It must NOT descend into a condition
    (``IfExp.test``) or comparison operands: a kind named only in
    ``A if state == events.ROUND_START else B`` is the *selector*, not an emitted
    value, and counting it would let a genuinely-unemitted kind masquerade as
    emitted — the exact false-negative this keystone invariant exists to forbid.
    """
    if isinstance(arg, ast.IfExp):
        return kind_constant_names(arg.body) | kind_constant_names(arg.orelse)
    if isinstance(arg, ast.BoolOp):
        names: set[str] = set()
        for value in arg.values:
            names |= kind_constant_names(value)
        return names
    if isinstance(arg, ast.Name):
        return {arg.id}
    if isinstance(arg, ast.Attribute):
        return {arg.attr}
    return set()
