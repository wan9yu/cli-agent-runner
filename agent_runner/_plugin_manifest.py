"""The plugin ABI: one typed, declared capability manifest per plugin, read
by the loader (agent_runner/__init__.py::load_and_register_plugins) instead
of relying on import-time register_*() side effects. One entry-point group,
`agent_runner.plugins`, each entry resolving to a module-level
`PLUGIN = PluginManifest(...)`.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass
from typing import Literal

from agent_runner._registry import ensure_unique
from agent_runner.hooks import PostRoundHook

# THE single, pinned string->signal.Signals table for cooperative_stop. Nothing
# else in the codebase may turn a cooperative-stop string into a signal (no
# `getattr(signal, arbitrary_string)`, which could reach SIGKILL=zero-grace or
# SIGSTOP=freeze). Keys ARE the full set of legal cooperative_stop values, so
# __post_init__ validates against these same keys.
_COOPERATIVE_SIGNALS: dict[str, signal.Signals] = {
    "SIGTERM": signal.SIGTERM,
    "SIGINT": signal.SIGINT,
}


@dataclass(frozen=True)
class PluginManifest:
    """A plugin's complete, declared capability set. `name` is the plugin's
    own identity — `[plugins] disable` keys on THIS, not on any individual
    hook's own `.name`."""

    name: str
    post_round_hooks: tuple[PostRoundHook, ...] = ()
    cooperative_stop: Literal["SIGTERM", "SIGINT"] | None = None
    """How this preset's CLI cooperatively drains/cleans up when the supervisor
    stops the round: the signal it drains on (``"SIGTERM"`` = gemini/pi,
    ``"SIGINT"`` = claude/codex), or ``None`` = no cooperative drain (the hard
    path). Source-verified per preset, NEVER assumed from a CLI's general
    reputation. Resolved to a ``signal.Signals`` ONLY by
    ``resolve_cooperative_signal``; a ``None`` cooperative_stop maps to
    SIGTERM-first at the reap site (today's behavior), never "skip the first
    signal"."""

    def __post_init__(self) -> None:
        # Errors-unlikely-by-construction: an unknown signal name (or a
        # freeze/zero-grace one like SIGSTOP/SIGKILL) is unrepresentable -- it
        # raises at construction, not at kill time.
        if self.cooperative_stop is not None and self.cooperative_stop not in _COOPERATIVE_SIGNALS:
            raise ValueError(
                "cooperative_stop must be one of {None, 'SIGTERM', 'SIGINT'}, "
                f"got {self.cooperative_stop!r}"
            )


_LOADED_MANIFESTS: list[PluginManifest] = []


def loaded_manifest_names() -> list[str]:
    """Names of every currently-registered manifest (order-preserving).
    Mirrors the ``list(_LOADED_MANIFESTS)`` pattern; used by the loader for
    idempotent re-load skipping."""
    return [m.name for m in _LOADED_MANIFESTS]


def _cooperative_stop_for_name(agent_binary: str | None) -> str | None:
    """The ``cooperative_stop`` field of the registered manifest named
    `agent_binary`, or None when no such manifest is registered (or it declares
    no cooperative stop). The single registry lookup the resolvers below share."""
    for m in _LOADED_MANIFESTS:
        if m.name == agent_binary:
            return m.cooperative_stop
    return None


def cooperative_stop_by_name() -> dict[str, str]:
    """`{manifest name: cooperative_stop signal name}` for every registered
    manifest declaring a cooperative_stop (order-preserving). Mirrors
    loaded_manifest_names; used by peek/doctor -- an operator sees not just
    WHICH presets cooperate but the SIGNAL each drains on."""
    return {m.name: m.cooperative_stop for m in _LOADED_MANIFESTS if m.cooperative_stop is not None}


def is_cooperative_agent(agent_binary: str | None) -> bool:
    """Whether the agent's preset declares a cooperative_stop. Routes through
    the same single-scan `resolve_cooperative_signal` uses: `cooperative_stop`
    is always in {None, "SIGTERM", "SIGINT"} (`__post_init__`-guaranteed) and
    `_COOPERATIVE_SIGNALS` keys are exactly {"SIGTERM", "SIGINT"}, so a
    resolved signal exists iff the named manifest declares a cooperative_stop."""
    return resolve_cooperative_signal(agent_binary) is not None


def resolve_cooperative_signal(agent_binary: str | None) -> signal.Signals | None:
    """The signal the agent named `agent_binary` cooperatively drains on, or
    None when its preset declares no cooperative_stop. THE ONLY place a
    cooperative_stop string becomes a ``signal.Signals`` (via the pinned
    ``_COOPERATIVE_SIGNALS`` table) -- sibling to resolve_sigterm_grace_s, and
    the SIGNAL half of the same anti-skew single-source: serve resolves it once
    per phase from its own registry and publishes the result via env, so the
    round child never re-derives (and skews) it."""
    return _COOPERATIVE_SIGNALS.get(_cooperative_stop_for_name(agent_binary))


def cooperative_signal_from_name(name: str | None) -> signal.Signals | None:
    """Map a published cooperative-stop signal NAME (e.g. from
    AGENT_RUNNER_COOPERATIVE_STOP_SIGNAL) back to a ``signal.Signals`` through
    the SAME pinned ``_COOPERATIVE_SIGNALS`` table. An unknown/absent name -> None
    (the reap site then defaults to SIGTERM-first, today's behavior). Never
    getattr(signal, ...): a leaked/hostile "SIGKILL"/"SIGSTOP" is simply not a
    table key and falls back safely."""
    return _COOPERATIVE_SIGNALS.get(name) if name is not None else None


def resolve_sigterm_grace_s(agent_binary: str | None, sigterm_grace_s: int) -> int:
    """The SIGTERM->SIGKILL grace this round's agent actually gets:
    `sigterm_grace_s` (config's [agent] sigterm_grace_s, boot-capped at
    _ROUND_TERM_GRACE_S) when `agent_binary` names a manifest declaring a
    cooperative_stop, else agent_runtime.REAP_GRACE_S (5s) unchanged. One
    function, two callers: cli.serve_cmd._apply_reap_grace_env (what the agent
    actually gets) and doctor/peek (what an operator sees)."""
    from agent_runner.agent_runtime import REAP_GRACE_S

    if is_cooperative_agent(agent_binary):
        return sigterm_grace_s
    return REAP_GRACE_S


def register_manifest(manifest: PluginManifest) -> None:
    """Register every capability a manifest declares into its own registry.
    No import-time side effects — the loader calls this explicitly after
    resolving a plugin's `PLUGIN` attribute.

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
    return found
