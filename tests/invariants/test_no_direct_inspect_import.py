"""Invariant: no module in ``agent_runner/`` imports ``inspect`` directly.

``inspect`` (and its ~0.95 MB ``ast``/``dis``/``tokenize``/``token``/``opcode``
tail) is on the ``import agent_runner.cli`` startup graph SOLELY because
``dataclasses`` imports it unconditionally on CPython 3.11 — nothing in
``agent_runner/`` imports it directly. That makes ``dataclasses`` the single
door ``inspect`` enters through, so removing ``dataclasses`` from the resident
process (the v0.4 breaking-release footprint lever recorded in
``docs/internal/followups/2026-09-15-footprint-floor-and-psutil-lazy.md``) would
actually drop the ``inspect`` tail.

A direct ``import inspect`` anywhere in ``agent_runner/`` would silently defeat
that: ``inspect`` would stay resident after ``dataclasses`` left, and the v0.4
lever would evaporate unnoticed. This pins the door shut. (Function-local
imports count too — a lazy ``import inspect`` still keeps the tail alive the
moment the function runs, and this is a footprint guard, not a startup-only one.)
"""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


def _direct_inspect_import_hits() -> list[str]:
    hits: list[str] = []
    for path in _PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "inspect" for a in node.names):
                hits.append(f"{path.relative_to(_PKG)}:{node.lineno} import inspect")
            elif isinstance(node, ast.ImportFrom) and node.module == "inspect":
                hits.append(f"{path.relative_to(_PKG)}:{node.lineno} from inspect import ...")
    return hits


def test_agent_runner_should_import_inspect_so_the_v04_dataclasses_lever_survives_when_invoked():
    hits = _direct_inspect_import_hits()

    assert not hits, (
        "inspect must enter agent_runner ONLY transitively via dataclasses — a direct "
        "import keeps its ~0.95 MB ast/dis/tokenize tail resident even after the v0.4 "
        "dataclasses-removal footprint lever, silently killing that lever. Offending imports:\n  "
        + "\n  ".join(hits)
    )
