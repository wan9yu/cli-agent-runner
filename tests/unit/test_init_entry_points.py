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
from agent_runner import events as _events
from agent_runner import hooks as _hooks
from agent_runner import monitor as _monitor
from agent_runner._plugin_manifest import PluginManifest
from agent_runner.config.models import PluginsConfig
from tests._test_helpers import isolating

_reset = isolating(
    _hooks._PRE_ROUND_HOOKS,
    _hooks._CONTEXT_ENRICHERS,
    _hooks._POST_ROUND_HOOKS,
    _events._PLUGIN_KINDS,
    _monitor._PLUGIN_DETECTORS,
)

# A real, importable PluginManifest that is NEVER scanned via the real
# agent_runner.plugins group -- so it stays "fresh" (not already loaded)
# for test_load_and_register_should_strip_extras_marker_and_register_when_resolved
# to resolve and register from scratch.
_FRESH_MANIFEST_FOR_LOADER_TEST = PluginManifest(
    name="test_init_entry_points_fresh_plugin",
    event_kinds=("test_init_entry_points_fresh_kind",),
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
    assert "test_init_entry_points_fresh_kind" in _events.KNOWN_EVENT_KINDS


def test_apply_plugin_disable_should_remove_named_pre_round_hook() -> None:
    """Disable keys on the PluginManifest's own `name`, not the hook's `.name` —
    the hook must be registered via a manifest for disable to find it."""
    from agent_runner import apply_plugin_disable, hooks
    from agent_runner._plugin_manifest import register_manifest

    pre_count_before = len(hooks._PRE_ROUND_HOOKS)

    class _TestHook:
        name = "test_disable_target"

        def before_round(self, ctx):
            pass

    register_manifest(PluginManifest(name="test_disable_target", pre_round_hooks=(_TestHook(),)))

    assert len(hooks._PRE_ROUND_HOOKS) == pre_count_before + 1
    assert any(h.name == "test_disable_target" for h in hooks._PRE_ROUND_HOOKS)

    apply_plugin_disable(["test_disable_target"])

    assert len(hooks._PRE_ROUND_HOOKS) == pre_count_before
    assert all(h.name != "test_disable_target" for h in hooks._PRE_ROUND_HOOKS)


def test_apply_plugin_disable_should_warn_when_name_not_installed(
    recwarn: pytest.WarningsChecker,
) -> None:
    from agent_runner import apply_plugin_disable

    apply_plugin_disable(["definitely_not_installed_xyz_unique_123"])

    warnings_text = " ".join(str(w.message) for w in recwarn.list)
    assert "definitely_not_installed_xyz_unique_123" in warnings_text


def test_apply_plugin_disable_should_prune_serve_startup_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[plugins] disable must filter _SERVE_STARTUP_HOOKS, just like other hook registries."""
    from agent_runner import apply_plugin_disable, hooks
    from agent_runner._plugin_manifest import register_manifest

    monkeypatch.setattr(hooks, "_SERVE_STARTUP_HOOKS", [])

    class GoodHook:
        name = "good_hook"

        def __call__(self, cfg) -> None:
            pass

    class BadHook:
        name = "bad_hook"

        def __call__(self, cfg) -> None:
            raise RuntimeError("would fail")

    hooks.register_serve_startup_hook(GoodHook())
    register_manifest(PluginManifest(name="bad_hook", serve_startup_hooks=(BadHook(),)))

    assert [h.name for h in hooks.serve_startup_hooks()] == ["good_hook", "bad_hook"]

    apply_plugin_disable(["bad_hook"])

    assert [h.name for h in hooks.serve_startup_hooks()] == ["good_hook"]


def test_disabled_plugin_names_should_return_names_from_last_apply_plugin_disable_call() -> None:
    from agent_runner import apply_plugin_disable, disabled_plugin_names
    from agent_runner._plugin_manifest import register_manifest

    class _TestHook2:
        name = "for_visibility_check"

        def before_round(self, ctx):
            pass

    register_manifest(PluginManifest(name="for_visibility_check", pre_round_hooks=(_TestHook2(),)))
    apply_plugin_disable(["for_visibility_check"])

    assert "for_visibility_check" in disabled_plugin_names()
