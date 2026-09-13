"""Agent Runner — restart-on-exit supervisor for autonomous CLI agents."""

from __future__ import annotations

try:
    from agent_runner._version import __version__
except ImportError:  # editable install before hatch-vcs has generated _version.py
    __version__ = "0.0.0+unknown"

_PLUGIN_GROUP = "agent_runner.plugins"

# Tracks the names passed to the most recent ``apply_plugin_disable`` call.
# Surfaced via peek --json `plugins.disabled` for operator visibility.
_DISABLED_PLUGIN_NAMES: list[str] = []


def _load_plugin_manifests() -> None:
    """Discover agent_runner.plugins entry points, resolve each to a
    module-level PluginManifest, and register its declared capabilities.

    Called at package import. A broken plugin (bad import, missing PLUGIN
    attribute, malformed manifest) must never crash the supervisor — isolated
    as a UserWarning.

    Discovery goes through the ``entry_points.txt`` scanner (cheaper than a
    fresh ``importlib.metadata.entry_points()`` scan per process; see
    ``_plugin_scan``), with a hard fallback to ``importlib.metadata`` on any
    parse failure.
    """
    import importlib
    import sys
    import warnings

    from agent_runner._plugin_manifest import register_manifest
    from agent_runner._plugin_scan import scan_entry_points

    for name, value in scan_entry_points(sys.path, _PLUGIN_GROUP):
        try:
            # An entry-point value may carry a trailing extras marker
            # (``module:attr [extra1,extra2]``); importlib.metadata.EntryPoint's
            # own module/attr grammar excludes "[", so anything from the first
            # "[" onward is always extras, never part of the path — strip it
            # before resolving, or it glues onto attr_path and breaks getattr().
            module_path, _, attr_path = value.partition("[")[0].rstrip().partition(":")
            mod = importlib.import_module(module_path)
            target = mod
            for attr in filter(None, attr_path.split(".")):
                target = getattr(target, attr)
            register_manifest(target)
        except Exception as e:
            warnings.warn(
                f"failed to load {_PLUGIN_GROUP} plugin {name!r}: {e}",
                stacklevel=3,
            )


_load_plugin_manifests()


def apply_plugin_disable(names: list[str]) -> None:
    """Remove plugins matching ``names`` (manifest names) from all in-memory
    registries.

    Called after config-load to honor ``[plugins] disable``. Idempotent for
    already-removed names. Emits a UserWarning for names that match no
    loaded manifest (typo catcher; tolerates cross-env config drift).

    Plugin packages still load at import time — this removes from the registries
    that the runner and peek consult. Side effects from loading (module-level
    imports, etc.) have already happened by the time this runs.

    Known limitation: vcs_state._PLUGIN_OWNED_PATHS lacks per-plugin name
    attribution today, so owned-paths are NOT filtered here. Disabled plugin's
    paths remain registered (mostly inert).
    """
    import warnings

    from agent_runner._plugin_manifest import unregister_by_name

    if not names:
        return

    global _DISABLED_PLUGIN_NAMES
    _DISABLED_PLUGIN_NAMES = list(names)

    found = unregister_by_name(set(names))
    unknown = set(names) - found
    if unknown:
        warnings.warn(
            f"[plugins] disable references unknown plugin name(s): {sorted(unknown)}. "
            f"(Names matched no loaded PluginManifest; check spelling or installed packages.)",
            stacklevel=2,
        )


def disabled_plugin_names() -> list[str]:
    """Names passed to the most recent ``apply_plugin_disable`` call.

    Used by peek --json to surface ``plugins.disabled`` for operator visibility.
    Returns the LIST (not set) preserving configured order.
    """
    return list(_DISABLED_PLUGIN_NAMES)
