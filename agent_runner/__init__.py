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


_DISCOVERED_PLUGIN_ENTRIES: list[tuple[str, str]] = []  # (name, "module:attr"), populated once


def _discover_plugin_manifests() -> None:
    """Package-import-time step. Scans entry_points.txt ONLY (scan_entry_points) —
    imports nothing, executes no plugin code.

    Discovery goes through the ``entry_points.txt`` scanner (cheaper than a
    fresh ``importlib.metadata.entry_points()`` scan per process; see
    ``_plugin_scan``), with a hard fallback to ``importlib.metadata`` on any
    parse failure.
    """
    import sys

    from agent_runner._plugin_scan import scan_entry_points

    global _DISCOVERED_PLUGIN_ENTRIES
    _DISCOVERED_PLUGIN_ENTRIES = scan_entry_points(sys.path, _PLUGIN_GROUP)


_discover_plugin_manifests()


def load_and_register_plugins(plugins_cfg) -> None:
    """Called from config.loader.load_config once [plugins] is parsed — the
    load-bearing fix: verification now has a Config to gate on. For each
    discovered (name, value) NOT in plugins_cfg.disable and not already
    registered, resolve module_path/attr_path, import + register_manifest. A
    disabled name is never imported. A per-plugin failure isolates as a
    UserWarning and skips that ONE plugin; it never aborts the whole load.
    """
    import importlib
    import warnings

    from agent_runner._plugin_manifest import loaded_manifest_names, register_manifest

    disable = set(plugins_cfg.disable)
    already = set(loaded_manifest_names())
    for name, value in _DISCOVERED_PLUGIN_ENTRIES:
        if name in disable or name in already:
            continue
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
        except Exception as e:  # noqa: BLE001 — a broken plugin must never crash the supervisor
            warnings.warn(f"failed to load {_PLUGIN_GROUP} plugin {name!r}: {e}", stacklevel=3)


def apply_plugin_disable(names: list[str]) -> None:
    """Remove plugins matching ``names`` (manifest names) from all in-memory
    registries.

    Called after config-load to honor ``[plugins] disable``. Idempotent for
    already-removed names. Emits a UserWarning for names that match no
    loaded manifest (typo catcher; tolerates cross-env config drift).

    A disabled name is never imported by ``load_and_register_plugins`` in the
    first place, so this is belt-and-suspenders cleanup for anything that
    reached the registries another way (e.g. a manifest registered directly
    via ``register_manifest``, not through entry-point discovery).

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
