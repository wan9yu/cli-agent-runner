"""The plugin ABI: one typed, declared capability manifest per plugin, read
by the loader (agent_runner/__init__.py::load_and_register_plugins) instead
of relying on import-time register_*() side effects. One entry-point group,
`agent_runner.plugins`, each entry resolving to a module-level
`PLUGIN = PluginManifest(...)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runner._registry import ensure_unique
from agent_runner.hooks import DirtyHandler, PostRoundHook, SpawnHook


@dataclass(frozen=True)
class PluginManifest:
    """A plugin's complete, declared capability set. `name` is the plugin's
    own identity — `[plugins] disable` keys on THIS, not on any individual
    hook's own `.name`."""

    name: str
    post_round_hooks: tuple[PostRoundHook, ...] = ()
    dirty_handlers: tuple[DirtyHandler, ...] = ()
    spawn_hooks: tuple[SpawnHook, ...] = ()
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


def is_cooperative_agent(agent_binary: str | None) -> bool:
    """Whether the agent's preset declares itself SIGTERM-cooperative.
    `None in [...]` is safely False, so no None-guard is needed."""
    return agent_binary in cooperative_manifest_names()


def resolve_sigterm_grace_s(agent_binary: str | None, sigterm_grace_s: int) -> int:
    """The SIGTERM->SIGKILL grace this round's agent actually gets:
    `sigterm_grace_s` (config's [agent] sigterm_grace_s, boot-capped at
    _ROUND_TERM_GRACE_S) when `agent_binary` names a manifest declaring
    sigterm_cooperative=True, else agent_runtime.REAP_GRACE_S (5s)
    unchanged. One function, two callers: cli.serve_cmd._apply_reap_grace_env
    (what the agent actually gets) and doctor/peek (what an operator sees)."""
    from agent_runner.agent_runtime import REAP_GRACE_S

    if is_cooperative_agent(agent_binary):
        return sigterm_grace_s
    return REAP_GRACE_S


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
    (e.g. two different plugins each declaring a post_round_hook named
    ``"foo"``); they have no visibility into the manifest's own top-level name, so two
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

    from agent_runner import hooks

    for h in manifest.post_round_hooks:
        hooks.register_post_round_hook(h)
    for d in manifest.dirty_handlers:
        hooks.register_dirty_handler(
            d, owner=manifest.name, builtin=builtin, module_path=module_path, attr_path=attr_path
        )
    for s in manifest.spawn_hooks:
        hooks.register_spawn_hook(
            s, owner=manifest.name, builtin=builtin, module_path=module_path, attr_path=attr_path
        )
    _LOADED_MANIFESTS.append(manifest)


def _remove_by_identity(registry: list, items: tuple) -> None:
    registry[:] = [x for x in registry if x not in items]


def unregister_by_name(names: set[str]) -> set[str]:
    """Remove every capability declared by manifests whose `.name` is in
    `names`. Returns the subset of `names` actually matched (for the
    caller's unknown-name warning)."""
    from agent_runner import hooks

    found: set[str] = set()
    for manifest in list(_LOADED_MANIFESTS):
        if manifest.name not in names:
            continue
        found.add(manifest.name)
        _remove_by_identity(hooks._POST_ROUND_HOOKS, manifest.post_round_hooks)
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
    return found
