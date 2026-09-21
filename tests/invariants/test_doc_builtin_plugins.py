"""Invariant: docs/plugins.md's built-in post_round_hooks section is complete.

The `agent_runner.plugins` entry-points table (resolved to live PluginManifests)
is the SSOT for what ships. The section's count and its per-plugin subsections
both drifted when codewhale landed in 0.1.41 -- a plugin author reading this
page cannot know codewhale_error_detector exists, nor that `[plugins] disable`
accepts its name.
"""

from __future__ import annotations

import importlib
import re
import tomllib

from agent_runner._plugin_manifest import PluginManifest
from tests._test_helpers import ROOT

REPO = ROOT


def _builtin_post_round_hook_plugin_names() -> set[str]:
    """Names of the manifests in agent_runner.plugins that declare at least
    one post_round_hook."""
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    entries = data["project"]["entry-points"]["agent_runner.plugins"]

    names = set()
    for name, target in entries.items():
        module_path, _, attr = target.partition(":")
        manifest = getattr(importlib.import_module(module_path), attr)
        assert isinstance(manifest, PluginManifest), f"{name}: {target} is not a PluginManifest"
        if manifest.post_round_hooks:
            names.add(name)
    return names


def test_plugins_doc_should_list_every_builtin_post_round_hook_when_scanned() -> None:
    names = _builtin_post_round_hook_plugin_names()

    text = (REPO / "docs/plugins.md").read_text(encoding="utf-8")
    section = text.split("## Built-in post_round_hooks", 1)[-1].split("\n## ", 1)[0]
    m = re.search(r"ships (\d+) built-in", section)
    assert m, (
        "docs/plugins.md no longer states a built-in post_round_hooks count as a "
        "digit (reworded? update this guard)"
    )
    assert int(m.group(1)) == len(names), (
        f"docs/plugins.md claims {m.group(1)} built-in post_round_hooks; "
        f"pyproject registers {len(names)}: {sorted(names)}"
    )

    missing = {n for n in names if f"`{n}`" not in section}

    assert not missing, (
        f"docs/plugins.md's built-in post_round_hooks section never names "
        f"{sorted(missing)} — a plugin author cannot discover it"
    )
