"""Agent Runner — restart-on-exit supervisor for autonomous CLI agents."""

from __future__ import annotations

from typing import Any

try:
    from agent_runner._version import __version__
except ImportError:  # editable install before hatch-vcs has generated _version.py
    __version__ = "0.0.0+unknown"

_HOOK_GROUPS = (
    "agent_runner.pre_round_hooks",
    "agent_runner.context_enrichers",
    "agent_runner.post_round_hooks",
    "agent_runner.serve_startup_hooks",
)

# Tracks the names passed to the most recent ``apply_plugin_disable`` call.
# Surfaced via peek --json `plugins.disabled` for operator visibility.
_DISABLED_PLUGIN_NAMES: list[str] = []



def _load_plugins_from_group(group: str) -> None:
    """Discover and load entry_points in ``group``, isolating per-plugin failures.

    Called at package import. A broken plugin must not crash the supervisor;
    each failure surfaces as a ``UserWarning``.
    """
    import warnings
    from importlib.metadata import entry_points

    for ep in entry_points(group=group):
        try:
            ep.load()
        except Exception as e:
            warnings.warn(
                f"failed to load {group} plugin {ep.name!r}: {e}",
                stacklevel=3,
            )


def _load_event_kind_plugins() -> None:
    """Load plugins that register custom event kinds via ``events.register_event_kind``."""
    _load_plugins_from_group("agent_runner.event_kinds")


def _load_hook_plugins() -> None:
    """Load plugins that register pre_round / context_enricher / post_round hooks."""
    for group in _HOOK_GROUPS:
        _load_plugins_from_group(group)


def _load_detector_plugins() -> None:
    """Load plugins that register custom monitor detectors via ``monitor.register_detector``."""
    _load_plugins_from_group("agent_runner.detectors")


_load_event_kind_plugins()
_load_hook_plugins()
_load_detector_plugins()


def _prune_by_name(registry: list[Any], desired: set[str], found: set[str]) -> None:
    """In-place: remove items from registry whose .name is in desired.

    Updates ``found`` with the names actually removed.
    """
    matching = [x.name for x in registry if x.name in desired]
    found.update(matching)
    registry[:] = [x for x in registry if x.name not in desired]


def apply_plugin_disable(names: list[str]) -> None:
    """Remove plugins matching ``names`` from all in-memory registries.

    Called after config-load to honor ``[plugins] disable``. Idempotent for
    already-removed names. Emits a UserWarning for names that match no
    registered plugin (typo catcher; tolerates cross-env config drift).

    Plugin packages still load at import time — this removes from the registries
    that the runner and peek consult. Side effects from ep.load() (module-level
    imports, etc.) have already happened by the time this runs.

    Known limitation: vcs_state._PLUGIN_OWNED_PATHS lacks per-plugin name
    attribution today, so owned-paths are NOT filtered here. Disabled plugin's
    paths remain registered (mostly inert).
    """
    import warnings

    from agent_runner import events, hooks, monitor

    if not names:
        return

    global _DISABLED_PLUGIN_NAMES
    _DISABLED_PLUGIN_NAMES = list(names)

    found: set[str] = set()
    desired = set(names)

    # Pre-round hooks
    _prune_by_name(hooks._PRE_ROUND_HOOKS, desired, found)

    # Context enrichers
    _prune_by_name(hooks._CONTEXT_ENRICHERS, desired, found)

    # Post-round hooks
    _prune_by_name(hooks._POST_ROUND_HOOKS, desired, found)

    # Serve-startup hooks
    _prune_by_name(hooks._SERVE_STARTUP_HOOKS, desired, found)

    # Plugin event kinds
    for name in list(events._PLUGIN_KINDS):
        if name in desired:
            del events._PLUGIN_KINDS[name]
            found.add(name)

    # Detectors
    _prune_by_name(monitor._PLUGIN_DETECTORS, desired, found)

    # vcs_state._PLUGIN_OWNED_PATHS has no name attribution today (see docstring above).
    # Disabled plugin's owned paths are not filtered.

    unknown = desired - found
    if unknown:
        warnings.warn(
            f"[plugins] disable references unknown entry_points: {sorted(unknown)}. "
            f"(Names matched no registered plugin; check spelling or installed packages.)",
            stacklevel=2,
        )


def disabled_plugin_names() -> list[str]:
    """Names passed to the most recent ``apply_plugin_disable`` call.

    Used by peek --json to surface ``plugins.disabled`` for operator visibility.
    Returns the LIST (not set) preserving configured order.
    """
    return list(_DISABLED_PLUGIN_NAMES)
