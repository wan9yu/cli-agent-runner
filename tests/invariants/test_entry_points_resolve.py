"""Invariant: every pyproject.toml entry_point in agent_runner.post_round_hooks
resolves to a live class with the expected protocol shape. Catches:

- Class renamed in source without pyproject update.
- Module moved without pyproject update.
- Entry-point name typo.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path


def _read_post_round_hook_entries() -> dict[str, str]:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["entry-points"]["agent_runner.post_round_hooks"]


def test_post_round_hook_entry_points_declared():
    entries = _read_post_round_hook_entries()
    assert entries, "pyproject.toml declares no agent_runner.post_round_hooks entries"
    assert len(entries) >= 2, f"expected >=2 entries, got {len(entries)}"


def test_each_entry_point_resolves_to_a_live_class():
    entries = _read_post_round_hook_entries()
    for name, target in entries.items():
        module_path, _, attr = target.partition(":")
        assert attr, f"{name}: malformed target {target!r} (expected 'module:attr')"
        mod = importlib.import_module(module_path)
        cls = getattr(mod, attr, None)
        assert cls is not None, f"{name}: {target} unresolvable — class missing"
        instance = cls()
        assert hasattr(instance, "after_round"), (
            f"{name}: {cls.__name__} lacks after_round (PostRoundHook protocol)"
        )


def test_canonical_entry_point_names_match_class_name_attribute():
    """For non-alias entries, the pyproject key must equal cls.name.
    `claude_rate_limit_detector` is a known back-compat alias and is skipped.
    """
    entries = _read_post_round_hook_entries()
    aliases = {"claude_rate_limit_detector"}  # legacy alias retained intentionally
    for name, target in entries.items():
        if name in aliases:
            continue
        module_path, _, attr = target.partition(":")
        cls = getattr(importlib.import_module(module_path), attr)
        instance = cls()
        assert instance.name == name, (
            f"entry_point '{name}' bound to class with name '{instance.name}' — mismatch"
        )
