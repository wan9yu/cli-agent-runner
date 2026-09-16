"""Invariant: every ``events.emit(...)`` call in core uses a kind that's in
``_BUILTIN_KINDS`` (core has no other kind to emit — plugin-declared event
kinds were removed in 0.3.9).
"""

from __future__ import annotations

import ast

from agent_runner import events
from tests.invariants._event_scan import PKG, emit_kind_args, kind_literals, package_modules


def test_emit_calls_in_core_should_use_builtin_kinds_when_scanned() -> None:
    """Scans via the shared helper: rglob (cli/ and builtin_plugins/ hold real emit
    sites) and alias-aware (monitor.py's emit_event, _emit.py's bare emit). A
    private copy of this scan is what let both blind spots survive.
    """
    bad_calls: list[tuple[str, int, str]] = []
    scanned = 0
    for path in package_modules():
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(PKG.parent).as_posix()
        for arg in emit_kind_args(tree):
            bad_calls.extend(
                (rel, lit.lineno, lit.value)
                for lit in kind_literals(arg)
                if lit.value not in events._BUILTIN_KINDS
            )

    assert scanned > 0, "no agent_runner modules scanned"  # vacuity-guard
    assert bad_calls == [], f"events.emit() with non-builtin kinds: {bad_calls}"
