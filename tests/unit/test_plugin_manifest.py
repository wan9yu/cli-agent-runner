"""Tests for the PluginManifest capability-declaration dataclass and its
registration/unregistration helpers (agent_runner/_plugin_manifest.py)."""

from __future__ import annotations

import pytest

from agent_runner import _plugin_manifest, hooks
from tests._test_helpers import isolating

_reset = isolating(
    hooks._POST_ROUND_HOOKS,
    _plugin_manifest._LOADED_MANIFESTS,
)


def test_register_manifest_should_populate_every_declared_registry_when_called():
    from agent_runner._plugin_manifest import PluginManifest, register_manifest

    class _Hook:
        name = "test_post_round_hook"

        def after_round(self, ctx, result):
            pass

    manifest = PluginManifest(name="test_plugin", post_round_hooks=(_Hook(),))

    register_manifest(manifest)

    assert any(h.name == "test_post_round_hook" for h in hooks.post_round_hooks())


def test_unregister_by_name_should_remove_only_the_named_manifests_capabilities_when_called():
    from agent_runner._plugin_manifest import PluginManifest, register_manifest, unregister_by_name

    class _Hook:
        name = "keep_me"

        def after_round(self, ctx, result):
            pass

    class _OtherHook:
        name = "drop_me"

        def after_round(self, ctx, result):
            pass

    register_manifest(PluginManifest(name="plugin_a", post_round_hooks=(_Hook(),)))
    register_manifest(PluginManifest(name="plugin_b", post_round_hooks=(_OtherHook(),)))

    found = unregister_by_name({"plugin_b"})

    assert found == {"plugin_b"}
    assert any(h.name == "keep_me" for h in hooks.post_round_hooks())
    assert not any(h.name == "drop_me" for h in hooks.post_round_hooks())


def test_register_manifest_should_raise_when_manifest_name_already_registered():
    from agent_runner._plugin_manifest import PluginManifest, register_manifest

    class _FirstHook:
        name = "first_hook"

        def after_round(self, ctx, result):
            pass

    class _SecondHook:
        name = "second_hook"

        def after_round(self, ctx, result):
            pass

    first = PluginManifest(name="dup_plugin", post_round_hooks=(_FirstHook(),))
    register_manifest(first)

    with pytest.raises(ValueError, match="dup_plugin"):
        register_manifest(PluginManifest(name="dup_plugin", post_round_hooks=(_SecondHook(),)))

    # The first manifest's own capability must stay intact — a rejected
    # second registration must not have touched the first's state, and the
    # colliding name must still resolve to exactly the first manifest.
    assert any(h.name == "first_hook" for h in hooks.post_round_hooks())
    assert not any(h.name == "second_hook" for h in hooks.post_round_hooks())
    assert [m for m in _plugin_manifest._LOADED_MANIFESTS if m.name == "dup_plugin"] == [first]


def test_plugin_manifest_should_default_sigterm_cooperative_to_false_when_omitted():
    from agent_runner._plugin_manifest import PluginManifest

    manifest = PluginManifest(name="x")

    assert manifest.sigterm_cooperative is False


def test_cooperative_manifest_names_should_list_only_manifests_declaring_true():
    from agent_runner._plugin_manifest import (
        PluginManifest,
        cooperative_manifest_names,
        register_manifest,
    )

    register_manifest(PluginManifest(name="cooperative_one", sigterm_cooperative=True))
    register_manifest(PluginManifest(name="not_cooperative"))

    assert cooperative_manifest_names() == ["cooperative_one"]


def test_builtin_presets_should_declare_sigterm_cooperative_only_for_gemini():
    from agent_runner.builtin_plugins import (
        claude_rate_limit,
        codewhale,
        gemini,
        kimi,
        pi,
    )

    assert gemini.PLUGIN.sigterm_cooperative is True
    assert claude_rate_limit.PLUGIN.sigterm_cooperative is False
    assert kimi.PLUGIN.sigterm_cooperative is False
    assert codewhale.PLUGIN.sigterm_cooperative is False
    assert pi.PLUGIN.sigterm_cooperative is False


def test_is_cooperative_agent_should_return_true_when_binary_names_a_cooperative_manifest():
    from agent_runner._plugin_manifest import (
        PluginManifest,
        is_cooperative_agent,
        register_manifest,
    )

    register_manifest(PluginManifest(name="fake_coop_2", sigterm_cooperative=True))

    assert is_cooperative_agent("fake_coop_2") is True


def test_is_cooperative_agent_should_return_false_when_binary_is_none():
    from agent_runner._plugin_manifest import is_cooperative_agent

    assert is_cooperative_agent(None) is False


def test_resolve_sigterm_grace_s_should_return_configured_grace_when_agent_is_cooperative():
    from agent_runner._plugin_manifest import (
        PluginManifest,
        register_manifest,
        resolve_sigterm_grace_s,
    )

    register_manifest(PluginManifest(name="fake_coop", sigterm_cooperative=True))

    assert resolve_sigterm_grace_s("fake_coop", 9) == 9


def test_resolve_sigterm_grace_s_should_return_default_when_agent_is_not_cooperative():
    from agent_runner._plugin_manifest import resolve_sigterm_grace_s
    from agent_runner.agent_runtime import REAP_GRACE_S

    assert resolve_sigterm_grace_s("claude", 9) == REAP_GRACE_S


def test_resolve_sigterm_grace_s_should_return_default_when_agent_binary_is_none():
    from agent_runner._plugin_manifest import resolve_sigterm_grace_s
    from agent_runner.agent_runtime import REAP_GRACE_S

    assert resolve_sigterm_grace_s(None, 9) == REAP_GRACE_S
