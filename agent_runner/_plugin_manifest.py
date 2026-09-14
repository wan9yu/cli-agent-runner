"""The plugin ABI: one typed, declared capability manifest per plugin, read
by the loader (agent_runner/__init__.py::load_and_register_plugins) instead
of relying on import-time register_*() side effects. One entry-point group,
`agent_runner.plugins`, each entry resolving to a module-level
`PLUGIN = PluginManifest(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runner._registry import ensure_unique
from agent_runner.api_types import Detector
from agent_runner.hooks import (
    ContextEnricher,
    DirtyHandler,
    PostRoundHook,
    PreRoundHook,
    ServeStartupHook,
    SpawnHook,
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
    spawn_hooks: tuple[SpawnHook, ...] = ()
    detectors: tuple[Detector, ...] = ()
    event_kinds: tuple[str, ...] = ()
    sigterm_cooperative: bool = False
    """Declares that this preset's CLI cooperatively drains/cleans up on
    SIGTERM -- source-verified per preset, NEVER assumed from a CLI's
    general reputation. Observability-only: surfaced via
    cooperative_manifest_names() to `peek --json` and `doctor`. Does not
    itself change any termination behavior."""


_LOADED_MANIFESTS: list[PluginManifest] = []


def loaded_manifest_names() -> list[str]:
    """Names of every currently-registered manifest (order-preserving).
    Mirrors the ``list(_LOADED_MANIFESTS)`` pattern; used by the loader for
    idempotent re-load skipping and by doctor for third-party hash listing."""
    return [m.name for m in _LOADED_MANIFESTS]


def cooperative_manifest_names() -> list[str]:
    """Names of every currently-registered manifest declaring
    sigterm_cooperative=True (order-preserving). Mirrors
    loaded_manifest_names; used by peek + doctor to answer "which presets
    are cooperative" without either caller reaching into _LOADED_MANIFESTS."""
    return [m.name for m in _LOADED_MANIFESTS if m.sigterm_cooperative]


def register_manifest(
    manifest: PluginManifest,
    *,
    builtin: bool = False,
    module_path: str = "",
    attr_path: str = "",
) -> None:
    """Register every capability a manifest declares into its own registry.
    No import-time side effects — the loader calls this explicitly after
    resolving a plugin's `PLUGIN` attribute.

    ``builtin`` records whether this manifest is a GENUINE builtin (verified by
    ``is_builtin_provenance`` at the load path) — it is threaded to
    ``register_dirty_handler`` so ``dispatch_dirty`` can grant in-process trust
    by handler-object identity rather than by the collidable manifest name.
    Defaults to False (fail-closed): any manifest registered outside the
    verified load path — a direct ``register_manifest`` call, a test — is
    treated as third-party and sandboxed.

    ``module_path``/``attr_path`` are the DISCOVERED entry point's resolvable
    location (``module:attr``); the loader threads them here so the trampoline
    re-imports a third-party dirty handler / spawn hook from that exact path,
    never from the collidable ``manifest.name`` — an entry-point name != the
    manifest name (legal for third-party plugins) then still resolves.

    Raises ``ValueError`` up front if ``manifest.name`` collides with an
    already-registered manifest's ``.name`` — this is checked HERE, not by
    the underlying registries' own ``ensure_unique`` calls below, which only
    catch two manifests sharing one of THEIR sub-capabilities' ``.name``
    (e.g. two different plugins each declaring a detector named ``"foo"``);
    they have no visibility into the manifest's own top-level name, so two
    manifests named e.g. ``"codewhale"`` with disjoint capability names would
    otherwise both register, and ``unregister_by_name``/``[plugins] disable``
    would then strip both when the operator meant to disable one.

    Appends to ``_LOADED_MANIFESTS`` only after every sub-registration
    succeeds — a duplicate CAPABILITY-name registration (e.g. a stray
    double-scan) raises via the underlying registries' own ``ensure_unique``
    checks before the manifest is recorded, so a failed call leaves no
    partial trace for ``unregister_by_name`` to later find and no-op
    against. This guarantee covers ``_LOADED_MANIFESTS`` only: a failure
    partway through the loop below (e.g. the 3rd of 5 declared
    post_round_hooks raises) still leaves the first 2 registered in their
    own per-family registry — matching the prior import-time register_*()
    semantics, where a mid-module exception left every earlier top-level
    call's side effect in place too.
    """
    ensure_unique(manifest.name, _LOADED_MANIFESTS, "plugin manifest")

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
        hooks.register_dirty_handler(
            d, owner=manifest.name, builtin=builtin, module_path=module_path, attr_path=attr_path
        )
    for s in manifest.spawn_hooks:
        hooks.register_spawn_hook(
            s, owner=manifest.name, builtin=builtin, module_path=module_path, attr_path=attr_path
        )
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
        for h in manifest.dirty_handlers:
            hooks._DIRTY_HANDLER_OWNER.pop(id(h), None)
            hooks._DIRTY_HANDLER_BUILTIN.pop(id(h), None)
            hooks._DIRTY_HANDLER_MODULE.pop(id(h), None)
        _remove_by_identity(hooks._SPAWN_HOOKS, manifest.spawn_hooks)
        for h in manifest.spawn_hooks:
            hooks._SPAWN_HOOK_OWNER.pop(id(h), None)
            hooks._SPAWN_HOOK_BUILTIN.pop(id(h), None)
            hooks._SPAWN_HOOK_MODULE.pop(id(h), None)
        _remove_by_identity(monitor._PLUGIN_DETECTORS, manifest.detectors)
        for kind in manifest.event_kinds:
            events._PLUGIN_KINDS.pop(kind, None)
    return found
