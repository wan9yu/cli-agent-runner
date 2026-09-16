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


def test_plugin_manifest_should_default_cooperative_stop_to_none_when_omitted():
    from agent_runner._plugin_manifest import PluginManifest

    manifest = PluginManifest(name="x")

    assert manifest.cooperative_stop is None


def test_manifest_should_reject_non_cooperative_signal():
    """Errors-unlikely-by-construction: a SIGKILL (zero grace) or any name
    outside {None, SIGTERM, SIGINT} is unrepresentable -- it raises at
    construction, never reaching the kill path."""
    from agent_runner._plugin_manifest import PluginManifest

    with pytest.raises(ValueError, match="cooperative_stop"):
        PluginManifest(name="x", cooperative_stop="SIGKILL")


def test_manifest_should_accept_the_two_cooperative_signals_and_none():
    from agent_runner._plugin_manifest import PluginManifest

    assert PluginManifest(name="a", cooperative_stop="SIGTERM").cooperative_stop == "SIGTERM"
    assert PluginManifest(name="b", cooperative_stop="SIGINT").cooperative_stop == "SIGINT"
    assert PluginManifest(name="c", cooperative_stop=None).cooperative_stop is None


def test_cooperative_stop_by_name_should_map_each_cooperative_preset_to_its_signal():
    from agent_runner._plugin_manifest import (
        PluginManifest,
        cooperative_stop_by_name,
        register_manifest,
    )

    register_manifest(PluginManifest(name="term_one", cooperative_stop="SIGTERM"))
    register_manifest(PluginManifest(name="int_one", cooperative_stop="SIGINT"))
    register_manifest(PluginManifest(name="hard_one"))

    assert cooperative_stop_by_name() == {"term_one": "SIGTERM", "int_one": "SIGINT"}


def test_builtin_presets_should_declare_the_researched_cooperative_stop_signals():
    from agent_runner.builtin_plugins import (
        claude_rate_limit,
        codewhale,
        gemini,
        kimi,
        pi,
    )

    assert gemini.PLUGIN.cooperative_stop == "SIGTERM"
    assert pi.PLUGIN.cooperative_stop == "SIGTERM"
    assert claude_rate_limit.PLUGIN.cooperative_stop == "SIGINT"
    assert kimi.PLUGIN.cooperative_stop is None
    assert codewhale.PLUGIN.cooperative_stop is None


def test_builtin_manifest_names_should_equal_their_agent_binary_so_the_signal_join_works():
    """The join is manifest.name == agent binary basename. claude's plugin is
    NAMED "claude" (not "claude_rate_limit") as of 0.3.9, so its declared SIGINT
    is NON-inert: resolve_cooperative_signal("claude") actually returns SIGINT.
    This is the mechanism->property closure of the v0.3.9 §B safety work."""
    import signal

    from agent_runner._plugin_manifest import (
        cooperative_stop_by_name,
        register_manifest,
        resolve_cooperative_signal,
    )
    from agent_runner.builtin_plugins import claude_rate_limit, gemini, pi

    for m in (gemini.PLUGIN, pi.PLUGIN, claude_rate_limit.PLUGIN):
        register_manifest(m)

    assert claude_rate_limit.PLUGIN.name == "claude"
    # The join now delivers the declared signal to the real agent binaries:
    assert resolve_cooperative_signal("claude") is signal.SIGINT
    assert resolve_cooperative_signal("gemini") is signal.SIGTERM
    assert resolve_cooperative_signal("pi") is signal.SIGTERM
    # peek/doctor key on the plugin name, which now == the binary -> non-inert.
    assert cooperative_stop_by_name()["claude"] == "SIGINT"


def test_is_cooperative_agent_should_return_true_when_binary_names_a_cooperative_manifest():
    from agent_runner._plugin_manifest import (
        PluginManifest,
        is_cooperative_agent,
        register_manifest,
    )

    register_manifest(PluginManifest(name="fake_coop_2", cooperative_stop="SIGTERM"))

    assert is_cooperative_agent("fake_coop_2") is True


def test_is_cooperative_agent_should_return_false_when_binary_is_none():
    from agent_runner._plugin_manifest import is_cooperative_agent

    assert is_cooperative_agent(None) is False


def test_resolve_cooperative_signal_should_map_the_declared_name_to_a_signal():
    import signal

    from agent_runner._plugin_manifest import (
        PluginManifest,
        register_manifest,
        resolve_cooperative_signal,
    )

    register_manifest(PluginManifest(name="int_agent", cooperative_stop="SIGINT"))
    register_manifest(PluginManifest(name="term_agent", cooperative_stop="SIGTERM"))

    assert resolve_cooperative_signal("int_agent") is signal.SIGINT
    assert resolve_cooperative_signal("term_agent") is signal.SIGTERM


def test_resolve_cooperative_signal_should_return_none_for_a_non_cooperative_or_unknown_agent():
    from agent_runner._plugin_manifest import (
        PluginManifest,
        register_manifest,
        resolve_cooperative_signal,
    )

    register_manifest(PluginManifest(name="hard_agent"))

    assert resolve_cooperative_signal("hard_agent") is None
    assert resolve_cooperative_signal("never_registered") is None
    assert resolve_cooperative_signal(None) is None


def test_cooperative_signal_from_name_should_map_only_the_two_pinned_names():
    import signal

    from agent_runner._plugin_manifest import cooperative_signal_from_name

    assert cooperative_signal_from_name("SIGTERM") is signal.SIGTERM
    assert cooperative_signal_from_name("SIGINT") is signal.SIGINT
    # A leaked / hostile name is simply not a table key -- never getattr(signal).
    assert cooperative_signal_from_name("SIGKILL") is None
    assert cooperative_signal_from_name("SIGSTOP") is None
    assert cooperative_signal_from_name(None) is None


def test_resolve_sigterm_grace_s_should_return_configured_grace_when_agent_is_cooperative():
    from agent_runner._plugin_manifest import (
        PluginManifest,
        register_manifest,
        resolve_sigterm_grace_s,
    )

    register_manifest(PluginManifest(name="fake_coop", cooperative_stop="SIGTERM"))

    assert resolve_sigterm_grace_s("fake_coop", 9) == 9


def test_resolve_sigterm_grace_s_should_return_default_when_agent_is_not_cooperative():
    from agent_runner._plugin_manifest import (
        PluginManifest,
        register_manifest,
        resolve_sigterm_grace_s,
    )
    from agent_runner.agent_runtime import REAP_GRACE_S

    register_manifest(PluginManifest(name="hard_agent"))

    assert resolve_sigterm_grace_s("hard_agent", 9) == REAP_GRACE_S


def test_resolve_sigterm_grace_s_should_return_default_when_agent_binary_is_none():
    from agent_runner._plugin_manifest import resolve_sigterm_grace_s
    from agent_runner.agent_runtime import REAP_GRACE_S

    assert resolve_sigterm_grace_s(None, 9) == REAP_GRACE_S
