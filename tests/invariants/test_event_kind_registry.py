"""Invariants for the event-kind registry.

Two guarantees:
1. Every ``events.emit(...)`` call in core uses a kind that's in ``_BUILTIN_KINDS``
   (core never emits plugin kinds; plugins emit their own kinds from their own code).
2. ``register_event_kind`` enforces the documented conflict rules: rejects collision
   with built-ins, rejects re-registration from a different source, idempotent for
   same source.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent_runner import events

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


@pytest.fixture(autouse=True)
def _reset_plugin_kinds():
    """Snapshot + restore the plugin registry around each test."""
    saved = events._PLUGIN_KINDS.copy()
    events._PLUGIN_KINDS.clear()
    yield
    events._PLUGIN_KINDS.clear()
    events._PLUGIN_KINDS.update(saved)


def test_given_emit_calls_in_core_when_scanned_then_kinds_are_builtin() -> None:
    """Core code only emits built-in kinds. Plugins emit plugin kinds from their own
    callsites (not in this package)."""
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
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "emit"
                and isinstance(target.value, ast.Name)
                and target.value.id == "events"
            ):
                if len(node.args) >= 2:
                    second = node.args[1]
                    if isinstance(second, ast.Constant) and isinstance(second.value, str):
                        if second.value not in events._BUILTIN_KINDS:
                            bad_calls.append((f.name, node.lineno, second.value))
    assert bad_calls == [], f"events.emit() with non-builtin kinds: {bad_calls}"


def test_given_register_collides_with_builtin_when_called_then_raises() -> None:
    with pytest.raises(ValueError, match="built-in"):
        events.register_event_kind("round_start", source="x")


def test_given_register_conflicts_with_different_source_when_called_then_raises() -> None:
    events.register_event_kind("conflict_name", source="src-a")
    with pytest.raises(ValueError, match="already registered"):
        events.register_event_kind("conflict_name", source="src-b")


def test_given_register_same_source_when_called_twice_then_idempotent() -> None:
    events.register_event_kind("idem_name", source="src-x")
    events.register_event_kind("idem_name", source="src-x")
    assert "idem_name" in events.KNOWN_EVENT_KINDS
