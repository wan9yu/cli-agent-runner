"""The plugin ABI: one typed, declared capability manifest per plugin, read
by the loader (agent_runner/__init__.py::_load_plugin_manifests) instead of
relying on import-time register_*() side effects. One entry-point group,
`agent_runner.plugins`, each entry resolving to a module-level
`PLUGIN = PluginManifest(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runner.api_types import Detector
from agent_runner.hooks import (
    ContextEnricher,
    DirtyHandler,
    PostRoundHook,
    PreRoundHook,
    ServeStartupHook,
)


@dataclass(frozen=True)
class PluginManifest:
    """A plugin's complete, declared capability set. `name` is the plugin's
    own identity — `[plugins] disable` keys on THIS, not on any individual
    hook/detector's own `.name`."""

    name: str
    pre_round_hooks: tuple[PreRoundHook, ...] = ()
    context_enrichers: tuple[ContextEnricher, ...] = ()
    post_round_hooks: tuple[PostRoundHook, ...] = ()
    serve_startup_hooks: tuple[ServeStartupHook, ...] = ()
    dirty_handlers: tuple[DirtyHandler, ...] = ()
    detectors: tuple[Detector, ...] = ()
    event_kinds: tuple[str, ...] = ()


_LOADED_MANIFESTS: list[PluginManifest] = []


def register_manifest(manifest: PluginManifest) -> None:
    """Register every capability a manifest declares into its own registry.
    No import-time side effects — the loader calls this explicitly after
    resolving a plugin's `PLUGIN` attribute.

    Appends to ``_LOADED_MANIFESTS`` only after every sub-registration
    succeeds — a duplicate-name registration (e.g. a stray double-scan)
    raises via the underlying registries' own ``ensure_unique`` checks
    before the manifest is recorded, so a failed call leaves no partial
    trace for ``unregister_by_name`` to later find and no-op against.
    This guarantee covers ``_LOADED_MANIFESTS`` only: a failure partway
    through the loop below (e.g. the 3rd of 5 declared post_round_hooks
    raises) still leaves the first 2 registered in their own per-family
    registry — matching the prior import-time register_*() semantics,
    where a mid-module exception left every earlier top-level call's
    side effect in place too.
    """
    from agent_runner import events, hooks, monitor

    for h in manifest.pre_round_hooks:
        hooks.register_pre_round_hook(h)
    for e in manifest.context_enrichers:
        hooks.register_context_enricher(e)
    for h in manifest.post_round_hooks:
        hooks.register_post_round_hook(h)
    for h in manifest.serve_startup_hooks:
        hooks.register_serve_startup_hook(h)
    for d in manifest.dirty_handlers:
        hooks.register_dirty_handler(d)
    for det in manifest.detectors:
        monitor.register_detector(det)
    for kind in manifest.event_kinds:
        events.register_event_kind(kind, source=manifest.name)
    _LOADED_MANIFESTS.append(manifest)


def _remove_by_identity(registry: list, items: tuple) -> None:
    registry[:] = [x for x in registry if x not in items]


def unregister_by_name(names: set[str]) -> set[str]:
    """Remove every capability declared by manifests whose `.name` is in
    `names`. Returns the subset of `names` actually matched (for the
    caller's unknown-name warning)."""
    from agent_runner import events, hooks, monitor

    found: set[str] = set()
    for manifest in list(_LOADED_MANIFESTS):
        if manifest.name not in names:
            continue
        found.add(manifest.name)
        _remove_by_identity(hooks._PRE_ROUND_HOOKS, manifest.pre_round_hooks)
        _remove_by_identity(hooks._CONTEXT_ENRICHERS, manifest.context_enrichers)
        _remove_by_identity(hooks._POST_ROUND_HOOKS, manifest.post_round_hooks)
        _remove_by_identity(hooks._SERVE_STARTUP_HOOKS, manifest.serve_startup_hooks)
        _remove_by_identity(hooks._DIRTY_HANDLERS, manifest.dirty_handlers)
        _remove_by_identity(monitor._PLUGIN_DETECTORS, manifest.detectors)
        for kind in manifest.event_kinds:
            events._PLUGIN_KINDS.pop(kind, None)
    return found
