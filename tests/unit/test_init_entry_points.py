"""Tests for agent_runner package entry_points loading.

Discovery itself (scan == importlib.metadata.entry_points per group, the
malformed-file fallback, the env override) is pinned by
tests/unit/test_plugin_scan_parity.py. These tests cover the loader built on
top of the scanner: per-plugin failure isolation, the groups queried, and a
successful load.
"""

from __future__ import annotations

import sys
import warnings
from unittest.mock import patch

import pytest

from agent_runner import events as _events
from agent_runner import hooks as _hooks
from agent_runner import monitor as _monitor
from tests._test_helpers import isolating

_reset = isolating(
    _hooks._PRE_ROUND_HOOKS,
    _hooks._CONTEXT_ENRICHERS,
    _hooks._POST_ROUND_HOOKS,
    _events._PLUGIN_KINDS,
    _monitor._PLUGIN_DETECTORS,
)


def test_load_event_kind_plugins_should_warn_when_plugin_import_fails() -> None:
    """A plugin import error must not crash the supervisor."""
    from agent_runner import _load_event_kind_plugins

    scanned = [("bad-plugin", "definitely_not_a_real_module_xyz:Attr")]

    with patch("agent_runner._plugin_scan.scan_entry_points", return_value=scanned):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _load_event_kind_plugins()
        assert any("bad-plugin" in str(w.message) for w in caught), (
            f"expected warning mentioning 'bad-plugin'; got {[str(w.message) for w in caught]}"
        )


def test_load_event_kind_plugins_should_not_warn_when_plugin_loads_cleanly() -> None:
    """A well-formed target (module importable, attribute present) loads clean --
    mirroring EntryPoint.load(): import the module, resolve the attribute, done."""
    from agent_runner import _load_event_kind_plugins

    scanned = [("good-plugin", "warnings:catch_warnings")]

    with patch("agent_runner._plugin_scan.scan_entry_points", return_value=scanned):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _load_event_kind_plugins()
        assert not caught, (
            f"expected no warnings for a good plugin; got {[str(w.message) for w in caught]}"
        )


def test_load_event_kind_plugins_should_strip_extras_marker_when_resolving_attr() -> None:
    """An entry-point value can carry a trailing extras marker
    (``module:attr [extra1,extra2]``, per importlib.metadata.EntryPoint's own
    grammar). The loader must strip it before resolving -- otherwise it glues
    onto the attribute path and getattr() fails, surfacing as a spurious
    UserWarning for an otherwise well-formed plugin."""
    from agent_runner import _load_event_kind_plugins

    scanned = [("extras-plugin", "warnings:catch_warnings [extra1,extra2]")]

    with patch("agent_runner._plugin_scan.scan_entry_points", return_value=scanned):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _load_event_kind_plugins()
        assert not caught, (
            f"expected the extras marker to be stripped and the plugin to load clean; "
            f"got {[str(w.message) for w in caught]}"
        )


def test_load_event_kind_plugins_should_query_event_kinds_group() -> None:
    from agent_runner import _load_event_kind_plugins

    with patch("agent_runner._plugin_scan.scan_entry_points", return_value=[]) as mock_scan:
        _load_event_kind_plugins()

    mock_scan.assert_called_once_with(sys.path, "agent_runner.event_kinds")


def test_load_hook_plugins_should_warn_when_plugin_import_fails() -> None:
    """Hook plugin import failures degrade to UserWarning, same as event_kinds."""
    from agent_runner import _load_hook_plugins

    scanned = [("bad-hook", "definitely_not_a_real_module_xyz:Attr")]

    with patch("agent_runner._plugin_scan.scan_entry_points", return_value=scanned):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _load_hook_plugins()
        assert any("bad-hook" in str(w.message) for w in caught), (
            f"expected warning mentioning 'bad-hook'; got {[str(w.message) for w in caught]}"
        )


def test_load_hook_plugins_should_query_all_five_hook_groups() -> None:
    from agent_runner import _load_hook_plugins

    call_groups: list[str] = []

    def fake_scan(sys_path, group):
        call_groups.append(group)
        return []

    with patch("agent_runner._plugin_scan.scan_entry_points", side_effect=fake_scan):
        _load_hook_plugins()

    assert sorted(call_groups) == sorted(
        [
            "agent_runner.pre_round_hooks",
            "agent_runner.context_enrichers",
            "agent_runner.post_round_hooks",
            "agent_runner.serve_startup_hooks",
            "agent_runner.dirty_handler_hooks",
        ]
    )


def test_load_detector_plugins_should_warn_when_plugin_import_fails() -> None:
    from agent_runner import _load_detector_plugins

    scanned = [("bad-detector", "definitely_not_a_real_module_xyz:Attr")]

    with patch("agent_runner._plugin_scan.scan_entry_points", return_value=scanned):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _load_detector_plugins()
        assert any("bad-detector" in str(w.message) for w in caught)


def test_load_detector_plugins_should_query_detectors_group() -> None:
    from agent_runner import _load_detector_plugins

    with patch("agent_runner._plugin_scan.scan_entry_points", return_value=[]) as mock_scan:
        _load_detector_plugins()

    mock_scan.assert_called_once_with(sys.path, "agent_runner.detectors")


def test_apply_plugin_disable_should_remove_named_pre_round_hook() -> None:
    from agent_runner import apply_plugin_disable, hooks

    pre_count_before = len(hooks._PRE_ROUND_HOOKS)

    class _TestHook:
        name = "test_disable_target"

        def before_round(self, ctx):
            pass

    hooks.register_pre_round_hook(_TestHook())

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
    hooks.register_serve_startup_hook(BadHook())

    assert [h.name for h in hooks.serve_startup_hooks()] == ["good_hook", "bad_hook"]

    apply_plugin_disable(["bad_hook"])

    assert [h.name for h in hooks.serve_startup_hooks()] == ["good_hook"]


def test_disabled_plugin_names_should_return_names_from_last_apply_plugin_disable_call() -> None:
    from agent_runner import apply_plugin_disable, disabled_plugin_names, hooks

    class _TestHook2:
        name = "for_visibility_check"

        def before_round(self, ctx):
            pass

    hooks.register_pre_round_hook(_TestHook2())
    apply_plugin_disable(["for_visibility_check"])

    assert "for_visibility_check" in disabled_plugin_names()
