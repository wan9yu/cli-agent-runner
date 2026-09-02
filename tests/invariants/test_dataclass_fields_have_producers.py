"""Invariant: every ``ProjectState`` / ``Status`` field has >=1 non-default producer.

``ProjectState.recent_rounds`` is always ``[]`` and ``Status.running`` is never
``True`` — hollow fields vulture cannot see (both names ARE used, just never
populated with real data). Vulture flags UNUSED names; it has no notion of "used
but always the same trivial value," so a vulture-whitelist entry would be the
wrong tool here.

This test scans ``agent_runner/`` for every call site constructing
``ProjectState(...)`` / ``Status(...)`` and, per field, checks whether at least
one call site supplies something other than the type's trivial/empty value
(``None``/``False``/``0``/``""``/``[]``/``{}``/``()``) — or a same-named
attribute pass-through (e.g. ``recent_rounds=base_state.recent_rounds``), which
merely relays an upstream field instead of producing new data.

A field with no non-default producer must be on ``_RESERVED`` with a reason,
AND the owning dataclass's docstring must say it is reserved (checked
separately) — the allow-list says "we know," the docstring says "so does
anyone reading the schema."
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from agent_runner import api_types, context_store

_PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"

_TARGET_CLASSES: dict[str, type] = {
    "ProjectState": api_types.ProjectState,
    "Status": context_store.Status,
}

# Fields with NO non-default producer today, allow-listed with a reason. Each
# entry here must ALSO be named "reserved" in the owning dataclass's own
# docstring (test_reserved_fields_documented_in_docstring below) — an entry
# here alone would be invisible to anyone reading api_types.py/context_store.py
# without this test file open.
_RESERVED: dict[tuple[str, str], str] = {
    ("ProjectState", "recent_rounds"): (
        "always [] until 0.3's round-history backfill; see monitor.assemble_project_state"
    ),
    ("Status", "running"): (
        "always False — status.json is only written between rounds "
        "(runner.py), never during one; 0.3 may add a live in-round write"
    ),
}


def _is_default_ish(node: ast.expr, field_name: str) -> bool:
    """True if `node` carries no real information for its field: a trivial
    literal (None/False/0/0.0/""), an empty container literal, or a same-named
    attribute pass-through that just relays an upstream field's value onward
    instead of producing a genuinely new one."""
    if isinstance(node, ast.Constant):
        return node.value in (None, False, 0, 0.0, "")
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, ast.Attribute) and node.attr == field_name:
        return True
    return False


def _producers(class_name: str, pkg: Path) -> dict[str, list[str]]:
    """Map field name -> [`path:line`, ...] for every call site under `pkg`
    that constructs `class_name(...)` with a non-default value for that
    field. A `**mapping` unpack call (e.g. rehydrating from a persisted JSON
    dict) carries no per-field static info and is skipped."""
    hits: dict[str, list[str]] = {}
    for path in sorted(pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            else:
                continue
            if name != class_name:
                continue
            for kw in node.keywords:
                if kw.arg is None:  # **mapping unpack
                    continue
                if not _is_default_ish(kw.value, kw.arg):
                    hits.setdefault(kw.arg, []).append(f"{path.name}:{node.lineno}")
    return hits


def _offending_fields(
    cls: type, class_name: str, pkg: Path, reserved: dict[tuple[str, str], str]
) -> list[str]:
    """Fields of `cls` with no non-default producer under `pkg` and no
    `reserved` allow-list entry. Shared by the real scan and the self-check
    below, so a fix to the algorithm can't accidentally diverge between them."""
    producers = _producers(class_name, pkg)
    return [
        f.name
        for f in dataclasses.fields(cls)
        if (class_name, f.name) not in reserved and not producers.get(f.name)
    ]


def _class_docstring(src: str, class_name: str) -> str:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return ast.get_docstring(node) or ""
    return ""


def test_every_projectstate_and_status_field_has_a_producer_or_is_reserved() -> None:
    offenders: list[str] = []
    checked = 0
    for class_name, cls in _TARGET_CLASSES.items():
        checked += len(dataclasses.fields(cls))
        offenders += [
            f"{class_name}.{name}" for name in _offending_fields(cls, class_name, _PKG, _RESERVED)
        ]
    assert checked > 0, "no ProjectState/Status fields discovered"  # vacuity-guard
    assert not offenders, (
        f"field(s) with no non-default producer in agent_runner/: {offenders} — "
        "either give the field a real producer, or add it to _RESERVED with a "
        "reason and mark it 'reserved' in the owning dataclass's docstring"
    )


def test_reserved_allowlist_names_real_fields() -> None:
    """Keep `_RESERVED` honest: no stale entries for renamed/removed fields."""
    for class_name, field_name in _RESERVED:
        names = {f.name for f in dataclasses.fields(_TARGET_CLASSES[class_name])}
        assert field_name in names, f"_RESERVED names {class_name}.{field_name}, no such field"


def test_reserved_fields_documented_in_docstring() -> None:
    """Every `_RESERVED` field must be named "reserved" + "0.3" in its owning
    dataclass's OWN docstring — not just in this test file's allow-list."""
    sources = {
        "ProjectState": (_PKG / "api_types.py").read_text(encoding="utf-8"),
        "Status": (_PKG / "context_store.py").read_text(encoding="utf-8"),
    }
    for class_name, field_name in _RESERVED:
        doc = _class_docstring(sources[class_name], class_name)
        assert field_name in doc and "reserved" in doc and "0.3" in doc, (
            f"{class_name}'s docstring must name {field_name!r} as reserved, "
            f"populated in 0.3 — got docstring: {doc!r}"
        )


def test_self_check_scan_flags_an_unlisted_always_default_field(tmp_path: Path) -> None:
    """Non-vacuousness proof: a synthetic dataclass with a field that is ALWAYS
    given its trivial default (``hollow=[]``) and a field that is genuinely
    populated (``real=n``) must be told apart by `_offending_fields` — with no
    allow-list entry the hollow one is flagged; allow-listing it quiets the
    guard. If this ever passed with an empty `hits`/non-empty `offenders`
    mismatch, the real invariant above would be vacuous."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "producer.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Widget:\n"
        "    hollow: list\n"
        "    real: int\n\n"
        "def build(n):\n"
        "    return Widget(hollow=[], real=n)\n"
    )

    @dataclasses.dataclass
    class Widget:
        hollow: list
        real: int

    unlisted = _offending_fields(Widget, "Widget", pkg, reserved={})
    assert unlisted == ["hollow"], (
        f"self-check: an un-allow-listed always-[] field must be flagged, got {unlisted}"
    )

    allowlisted = _offending_fields(
        Widget, "Widget", pkg, reserved={("Widget", "hollow"): "test fixture"}
    )
    assert allowlisted == [], (
        f"self-check: allow-listing the hollow field must quiet the guard, got {allowlisted}"
    )


def test_self_check_pass_through_is_not_mistaken_for_a_producer(tmp_path: Path) -> None:
    """A same-named attribute relay (`recent_rounds=base_state.recent_rounds`)
    forwards whatever the upstream field already holds — it must NOT count as
    a producer, or the real invariant would silently pass for exactly the bug
    this test exists to catch."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "relay.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Widget:\n"
        "    hollow: list\n\n"
        "def relay(base):\n"
        "    return Widget(hollow=base.hollow)\n"
    )
    hits = _producers("Widget", pkg)
    assert "hollow" not in hits, f"same-named attribute pass-through must not count: {hits}"
