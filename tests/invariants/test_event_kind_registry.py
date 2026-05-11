"""Every events.emit() call must use a kind that's in KNOWN_EVENT_KINDS.

Prevents typos and 'forgot to register new event' regressions.
"""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


def test_given_emit_calls_in_codebase_when_scanned_then_kinds_are_in_registry() -> None:
    from agent_runner.events import KNOWN_EVENT_KINDS

    bad_calls: list[tuple[str, int, str]] = []
    for f in PKG.glob("*.py"):
        if f.name == "events.py":
            continue
        text = f.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            # match: events.emit(...)
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "emit"
                and isinstance(target.value, ast.Name)
                and target.value.id == "events"
            ):
                if len(node.args) >= 2:
                    second = node.args[1]
                    if isinstance(second, ast.Constant) and isinstance(second.value, str):
                        if second.value not in KNOWN_EVENT_KINDS:
                            bad_calls.append((f.name, node.lineno, second.value))
    assert bad_calls == [], f"events.emit() with unregistered kinds: {bad_calls}"
