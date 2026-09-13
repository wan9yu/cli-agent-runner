"""Tests for the PluginManifest capability-declaration dataclass and its
registration/unregistration helpers (agent_runner/_plugin_manifest.py)."""

from __future__ import annotations

from agent_runner import events as _events
from agent_runner import hooks, monitor
from tests._test_helpers import isolating

_reset = isolating(
    hooks._PRE_ROUND_HOOKS,
    hooks._CONTEXT_ENRICHERS,
    hooks._POST_ROUND_HOOKS,
    hooks._SERVE_STARTUP_HOOKS,
    hooks._DIRTY_HANDLERS,
    _events._PLUGIN_KINDS,
    monitor._PLUGIN_DETECTORS,
)


def test_register_manifest_should_populate_every_declared_registry_when_called():
    from agent_runner._plugin_manifest import PluginManifest, register_manifest

    class _Hook:
        name = "test_post_round_hook"

        def after_round(self, ctx, result):
            pass

    manifest = PluginManifest(
        name="test_plugin", post_round_hooks=(_Hook(),), event_kinds=("test_kind",)
    )

    register_manifest(manifest)

    assert any(h.name == "test_post_round_hook" for h in hooks.post_round_hooks())
    assert "test_kind" in _events.KNOWN_EVENT_KINDS
