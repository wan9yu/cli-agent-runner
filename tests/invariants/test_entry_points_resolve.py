"""Invariant: every pyproject.toml entry_point in agent_runner.plugins
resolves to a live PluginManifest whose declared capabilities carry the
right protocol shape, and whose entry-point key matches manifest.name."""

from __future__ import annotations

import importlib
import tomllib

from agent_runner._plugin_manifest import PluginManifest
from tests._test_helpers import ROOT


def _read_plugin_entries() -> dict[str, str]:
    pyproject = ROOT / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["entry-points"]["agent_runner.plugins"]


def test_plugin_entries_should_be_declared_when_invoked():
    entries = _read_plugin_entries()

    assert entries, "pyproject.toml declares no agent_runner.plugins entries"

    assert len(entries) >= 2, f"expected >=2 entries, got {len(entries)}"


def test_entry_points_should_resolve_to_live_plugin_manifests_when_invoked():
    entries = _read_plugin_entries()

    broken = []
    for name, target in entries.items():
        module_path, _, attr = target.partition(":")
        if not attr:
            broken.append(f"{name}: malformed target {target!r} (expected 'module:attr')")
            continue
        manifest = getattr(importlib.import_module(module_path), attr, None)
        if not isinstance(manifest, PluginManifest):
            broken.append(f"{name}: {target} does not resolve to a PluginManifest")

    assert broken == []


def test_entry_point_names_should_match_manifest_name_when_invoked():
    entries = _read_plugin_entries()

    mismatched = []
    for name, target in entries.items():
        module_path, _, attr = target.partition(":")
        manifest = getattr(importlib.import_module(module_path), attr)
        if manifest.name != name:
            mismatched.append(f"{name} bound to {manifest.name}")

    assert mismatched == []


def test_legacy_claude_rate_limit_detector_alias_should_stay_removed_when_invoked():
    """`claude_rate_limit_detector` alias (0.1.20-0.1.34) hard-removed in 0.1.35.
    Consumers using the old name in `[plugins] disable/enable` must migrate.
    Re-pinned for the `agent_runner.plugins` group after the 0.3.0 collapse
    from `agent_runner.post_round_hooks` -- the guard was dropped in that
    rewrite; this restores it so the alias can never quietly reappear.
    """
    entries = _read_plugin_entries()

    actual = "claude_rate_limit_detector"

    assert actual not in entries, "0.1.20-era alias should be gone"


def test_declared_post_round_hooks_should_carry_after_round_when_resolved():
    entries = _read_plugin_entries()

    missing = []
    for name, target in entries.items():
        module_path, _, attr = target.partition(":")
        manifest = getattr(importlib.import_module(module_path), attr)
        missing.extend(
            f"{name}: post_round_hook lacks after_round"
            for hook in manifest.post_round_hooks
            if not hasattr(hook, "after_round")
        )

    assert missing == []
