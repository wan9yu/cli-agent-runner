"""Invariant: no bare int()/float() on a parsed-event dict's .get(...).

A plugin-controlled event field (e.g. ``reset_at_epoch``) can be None or a
non-numeric string. ``int(ev.get("reset_at_epoch", 0))`` crashes the serve
loop on that input; every such site must route through
``agent_runner._throttle._coerce_int`` / ``_coerce_float`` instead, which
degrade to a safe default with a UserWarning (never invent a value).
"""

from __future__ import annotations

import ast

from tests.invariants._event_scan import PKG

_SCANNED = [
    "_throttle.py",
    "monitor.py",
    "_monitor_detectors.py",
    "_monitor_registry.py",
    "_monitor_state.py",
]


def _bare_int_or_float_on_get(tree: ast.Module) -> list[int]:
    """Line numbers of every ``int(x.get(...))`` / ``float(x.get(...))`` call in ``tree``."""
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in ("int", "float"):
            continue
        arg = node.args[0] if node.args else None
        # int(x.get(...)) / float(x.get(...)) on a parsed event dict is the poison pill.
        if (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "get"
        ):
            offenders.append(node.lineno)
    return offenders


def test_scanner_detects_a_planted_offender() -> None:
    """Positive control: prove the AST match actually fires on the exact anti-pattern
    it's meant to catch, so a scanner bug (e.g. a typo'd attr name) can't make the
    real test below pass vacuously forever."""
    tree = ast.parse('reset_at = int(detected.get("reset_at_epoch", 0))\n')
    offenders = _bare_int_or_float_on_get(tree)
    assert offenders == [1], (  # vacuity-guard
        "scanner failed to detect a planted bare int(...get(...))"
    )


def test_no_bare_int_or_float_on_event_get() -> None:
    offenders: list[tuple[str, int]] = []
    for name in _SCANNED:
        path = PKG / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno in _bare_int_or_float_on_get(tree):
            offenders.append((name, lineno))
    assert offenders == [], f"bare int()/float() on event .get(): {offenders}"
