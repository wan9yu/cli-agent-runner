"""Invariant: every pyproject.toml entry_point in agent_runner.plugins
resolves to a live PluginManifest whose declared capabilities carry the
right protocol shape, and whose entry-point key matches manifest.name."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

from agent_runner._plugin_manifest import PluginManifest


def _read_plugin_entries() -> dict[str, str]:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["entry-points"]["agent_runner.plugins"]


def test_plugin_entries_should_be_declared():
    entries = _read_plugin_entries()

    assert entries, "pyproject.toml declares no agent_runner.plugins entries"
    assert len(entries) >= 2, f"expected >=2 entries, got {len(entries)}"


def test_entry_points_should_resolve_to_live_plugin_manifests():
    entries = _read_plugin_entries()

    for name, target in entries.items():
        module_path, _, attr = target.partition(":")
        assert attr, f"{name}: malformed target {target!r} (expected 'module:attr')"
        mod = importlib.import_module(module_path)
        manifest = getattr(mod, attr, None)
        assert isinstance(manifest, PluginManifest), (
            f"{name}: {target} does not resolve to a PluginManifest"
        )


def test_entry_point_names_should_match_manifest_name():
    entries = _read_plugin_entries()

    for name, target in entries.items():
        module_path, _, attr = target.partition(":")
        manifest = getattr(importlib.import_module(module_path), attr)
        assert manifest.name == name, (
            f"entry_point '{name}' bound to manifest with name '{manifest.name}' — mismatch"
        )


def test_declared_post_round_hooks_should_carry_after_round_when_resolved():
    entries = _read_plugin_entries()

    for name, target in entries.items():
        module_path, _, attr = target.partition(":")
        manifest = getattr(importlib.import_module(module_path), attr)
        for hook in manifest.post_round_hooks:
            assert hasattr(hook, "after_round"), f"{name}: post_round_hook lacks after_round"
