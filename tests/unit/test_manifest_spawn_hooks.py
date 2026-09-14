from __future__ import annotations

from agent_runner import _plugin_manifest, hooks
from agent_runner._plugin_manifest import (
    PluginManifest,
    register_manifest,
    unregister_by_name,
)
from agent_runner.api_types import SpawnDecision
from tests._test_helpers import isolating

_reset = isolating(
    hooks._SPAWN_HOOKS,
    hooks._SPAWN_HOOK_OWNER,
    hooks._SPAWN_HOOK_BUILTIN,
    _plugin_manifest._LOADED_MANIFESTS,
)


class _Gate:
    name = "acme_gate"

    def before_spawn(self, ctx, view):
        return SpawnDecision("proceed")


def test_register_manifest_should_register_declared_spawn_hook():
    gate = _Gate()

    register_manifest(PluginManifest(name="acme", spawn_hooks=(gate,)))

    assert hooks.spawn_hooks() == [gate]
    assert hooks._SPAWN_HOOK_OWNER[id(gate)] == "acme"


def test_register_manifest_should_confine_third_party_spawn_hook_fail_closed():
    gate = _Gate()

    register_manifest(PluginManifest(name="acme", spawn_hooks=(gate,)), builtin=False)

    assert hooks._SPAWN_HOOK_BUILTIN[id(gate)] is False


def test_register_manifest_should_key_builtin_trust_on_object_when_declared_builtin():
    gate = _Gate()

    register_manifest(PluginManifest(name="acme", spawn_hooks=(gate,)), builtin=True)

    assert hooks._SPAWN_HOOK_BUILTIN[id(gate)] is True


def test_unregister_by_name_should_remove_declared_spawn_hook_and_provenance():
    gate = _Gate()
    register_manifest(PluginManifest(name="acme", spawn_hooks=(gate,)))

    unregister_by_name({"acme"})

    assert hooks.spawn_hooks() == []
    assert id(gate) not in hooks._SPAWN_HOOK_OWNER
    assert id(gate) not in hooks._SPAWN_HOOK_BUILTIN


def test_register_manifest_should_stay_additive_when_no_spawn_hooks_declared():
    register_manifest(PluginManifest(name="acme"))

    assert hooks.spawn_hooks() == []
