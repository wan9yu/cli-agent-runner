from __future__ import annotations

import agent_runner
from agent_runner import _plugin_manifest, hooks
from agent_runner._plugin_manifest import PluginManifest, loaded_manifest_names
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import isolating

_reset = isolating(
    hooks._POST_ROUND_HOOKS,
    _plugin_manifest._LOADED_MANIFESTS,
)


class _Marker:
    name = "marker_hook"

    def after_round(self, ctx, result):
        pass


PLUGIN = PluginManifest(name="task1_probe", post_round_hooks=(_Marker(),))


def test_discover_should_register_no_manifest_when_called():
    agent_runner._discover_plugin_manifests()

    assert "task1_probe" not in loaded_manifest_names()

    assert "pi" not in loaded_manifest_names()


def test_load_and_register_should_import_and_register_when_not_disabled(monkeypatch):
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("task1_probe", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(PluginsConfig(disable=[]))

    assert "task1_probe" in loaded_manifest_names()
    assert any(h.name == "marker_hook" for h in hooks.post_round_hooks())


def test_load_and_register_should_skip_import_when_name_disabled(monkeypatch):
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("task1_probe", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(PluginsConfig(disable=["task1_probe"]))

    assert "task1_probe" not in loaded_manifest_names()


def test_load_and_register_should_be_idempotent_when_called_twice(monkeypatch):
    monkeypatch.setattr(
        agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [("task1_probe", f"{__name__}:PLUGIN")]
    )

    agent_runner.load_and_register_plugins(PluginsConfig(disable=[]))
    agent_runner.load_and_register_plugins(PluginsConfig(disable=[]))

    assert loaded_manifest_names().count("task1_probe") == 1
