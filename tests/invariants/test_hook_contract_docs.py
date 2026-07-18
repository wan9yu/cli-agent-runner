"""Invariants: the hook-contract docs must match hooks.py and runner.py.

Two of these were false in the module docstring that IS the plugin-author
contract, and one falsified a completeness claim in thesis.md.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _protocol_names() -> set[str]:
    """Every Protocol hooks.py declares — the extension points."""
    src = (REPO / "agent_runner/hooks.py").read_text(encoding="utf-8")
    return {
        node.name
        for node in ast.parse(src).body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(b, ast.Name) and b.id == "Protocol" for b in node.bases)
    }


def _emitted_hook_kinds() -> set[str]:
    """Every hook_kind= literal actually passed to a call."""
    out: set[str] = set()
    for path in (REPO / "agent_runner").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "hook_kind" and isinstance(kw.value, ast.Constant):
                        out.add(kw.value.value)
    return out


def test_given_hooks_module_docstring_when_scanned_then_lists_every_protocol() -> None:
    """hooks.py's docstring is the plugin-author contract — its count and its
    bullet list must both name every Protocol."""
    import agent_runner.hooks as hooks

    doc = hooks.__doc__ or ""
    protocols = _protocol_names()

    words = {"Three": 3, "Four": 4, "Five": 5, "Six": 6}
    m = re.search(r"\b(Three|Four|Five|Six) Protocol-typed extension points\b", doc)
    assert m, "hooks.py docstring no longer states an extension-point count"
    assert words[m.group(1)] == len(protocols), (
        f"hooks.py says {m.group(1)} extension points; hooks.py declares "
        f"{len(protocols)}: {sorted(protocols)}"
    )
    missing = {p for p in protocols if p not in doc}
    assert not missing, f"hooks.py docstring does not name {sorted(missing)}"


def test_given_thesis_hook_list_when_scanned_then_names_every_protocol() -> None:
    """thesis.md:26 claims 'That's the complete scope' — the list must be complete."""
    text = (REPO / "docs/thesis.md").read_text(encoding="utf-8")
    section = text.split("Exposes **plugin hooks**", 1)[-1].split("That's the complete scope", 1)[0]
    missing = {p for p in _protocol_names() if p not in section}
    assert not missing, (
        f"docs/thesis.md's hook list omits {sorted(missing)} yet claims completeness"
    )


def test_given_hook_failed_doc_when_compared_then_lists_every_emitted_kind() -> None:
    """docs/plugins.md documents the hook_failed payload's hook_kind values."""
    text = (REPO / "docs/plugins.md").read_text(encoding="utf-8")
    m = re.search(r'"hook_kind":\s*"([^"]+)"', text)
    assert m, "docs/plugins.md no longer shows a hook_kind payload line"
    documented = {v.strip() for v in m.group(1).split("|")}
    emitted = _emitted_hook_kinds()
    assert documented == emitted, (
        f"docs/plugins.md documents hook_kind {sorted(documented)}; code emits {sorted(emitted)}"
    )
