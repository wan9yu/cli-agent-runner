"""Tests for agent_runner package plugin discovery + loading.

Discovery itself (scan == importlib.metadata.entry_points per group, the
malformed-file fallback, the env override) is pinned by
tests/unit/test_plugin_scan_parity.py. These tests cover the two-phase
loader built on top of the scanner: `_discover_plugin_manifests` (import-time,
scan-only) and `load_and_register_plugins` (config-load-time, verify+import) --
per-plugin failure isolation, the single group queried, and
apply_plugin_disable's observable behavior.
"""

from __future__ import annotations

import sys
import warnings
from unittest.mock import patch

import pytest

import agent_runner
from agent_runner import hooks as _hooks
from agent_runner._plugin_manifest import PluginManifest
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import isolating

_reset = isolating(_hooks._POST_ROUND_HOOKS)


class _FreshHookForLoaderTest:
    name = "test_init_entry_points_fresh_hook"

    def after_round(self, ctx, result):
        pass


# A real, importable PluginManifest that is NEVER scanned via the real
# agent_runner.plugins group -- so it stays "fresh" (not already loaded)
# for test_load_and_register_should_strip_extras_marker_and_register_when_resolved
# to resolve and register from scratch.
_FRESH_MANIFEST_FOR_LOADER_TEST = PluginManifest(
    name="test_init_entry_points_fresh_plugin",
    post_round_hooks=(_FreshHookForLoaderTest(),),
)


def test_discover_should_query_the_single_plugins_group_when_called(monkeypatch) -> None:
    monkeypatch.setattr(agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", [])

    with patch("agent_runner._plugin_scan.scan_entry_points", return_value=[]) as mock_scan:
        agent_runner._discover_plugin_manifests()

    mock_scan.assert_called_once_with(sys.path, "agent_runner.plugins")


def test_load_and_register_should_warn_when_plugin_import_fails(monkeypatch) -> None:
    scanned = [("bad-plugin", "definitely_not_a_real_module_xyz:PLUGIN")]
    monkeypatch.setattr(agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", scanned)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent_runner.load_and_register_plugins(PluginsConfig(disable=[]))

    assert any("bad-plugin" in str(w.message) for w in caught)


def test_load_and_register_should_strip_extras_marker_and_register_when_resolved(
    monkeypatch,
) -> None:
    """A well-formed target can carry a trailing extras marker
    (``module:attr [extra1,extra2]``, per importlib.metadata.EntryPoint's own
    grammar) -- the loader must strip it before resolving, then actually
    register the resolved manifest's declared capabilities. Uses a manifest
    that is never scanned via the real entry-point group, so this proves a
    genuine first-time registration, not the already-loaded no-op re-scan
    that a real builtin's name would exercise instead."""
    from agent_runner._plugin_manifest import _LOADED_MANIFESTS

    before = len(_LOADED_MANIFESTS)
    target = "tests.unit.test_init_entry_points:_FRESH_MANIFEST_FOR_LOADER_TEST [extra1,extra2]"
    scanned = [("fresh-plugin", target)]
    monkeypatch.setattr(agent_runner, "_DISCOVERED_PLUGIN_ENTRIES", scanned)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent_runner.load_and_register_plugins(PluginsConfig(disable=[]))

    assert not caught, (
        f"expected the extras marker to be stripped and the plugin to load clean; "
        f"got {[str(w.message) for w in caught]}"
    )
    assert len(_LOADED_MANIFESTS) == before + 1
    assert any(h.name == "test_init_entry_points_fresh_hook" for h in _hooks.post_round_hooks())


def test_apply_plugin_disable_should_remove_named_post_round_hook_when_invoked() -> None:
    """Disable keys on the PluginManifest's own `name`, not the hook's `.name` —
    the hook must be registered via a manifest for disable to find it."""
    from agent_runner import apply_plugin_disable, hooks
    from agent_runner._plugin_manifest import register_manifest

    count_before = len(hooks._POST_ROUND_HOOKS)

    class _TestHook:
        name = "test_disable_target"

        def after_round(self, ctx, result):
            pass

    register_manifest(PluginManifest(name="test_disable_target", post_round_hooks=(_TestHook(),)))

    assert len(hooks._POST_ROUND_HOOKS) == count_before + 1
    assert any(h.name == "test_disable_target" for h in hooks._POST_ROUND_HOOKS)

    apply_plugin_disable(["test_disable_target"])

    assert len(hooks._POST_ROUND_HOOKS) == count_before
    assert all(h.name != "test_disable_target" for h in hooks._POST_ROUND_HOOKS)


def test_apply_plugin_disable_should_warn_when_name_not_installed(
    recwarn: pytest.WarningsChecker,
) -> None:
    from agent_runner import apply_plugin_disable

    apply_plugin_disable(["definitely_not_installed_xyz_unique_123"])

    warnings_text = " ".join(str(w.message) for w in recwarn.list)
    assert "definitely_not_installed_xyz_unique_123" in warnings_text


def test_disabled_plugin_names_should_return_last_apply_plugin_disable_names_when_invoked() -> None:
    from agent_runner import apply_plugin_disable, disabled_plugin_names
    from agent_runner._plugin_manifest import register_manifest

    class _TestHook2:
        name = "for_visibility_check"

        def after_round(self, ctx, result):
            pass

    register_manifest(PluginManifest(name="for_visibility_check", post_round_hooks=(_TestHook2(),)))
    apply_plugin_disable(["for_visibility_check"])

    assert "for_visibility_check" in disabled_plugin_names()
