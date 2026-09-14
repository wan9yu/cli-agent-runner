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


def _entry_point_module_path(value: str) -> str:
    """The resolvable module-path portion of an entry-point ``value``
    (``"module.path:attr [extras]"``), with any ``:attr`` suffix and trailing
    extras marker stripped.

    Shared by ``load_and_register_plugins``'s import resolution and
    ``doctor``'s per-plugin checksum print (``cli/doctor_cmd.py``) — the
    checksum print and the actual import MUST resolve the identical module,
    or an operator's pinned digest would silently protect the wrong file.
    """
    return value.partition("[")[0].rstrip().partition(":")[0]


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


def load_and_register_plugins(plugins_cfg, log_dir=None) -> None:
    """Called from config.loader.load_config once [plugins] is parsed — the
    load-bearing fix: verification now has a Config to gate on. For each
    discovered (name, value) NOT in plugins_cfg.disable and not already
    registered, resolve module_path/attr_path, import + register_manifest. A
    disabled name is never imported. A per-plugin failure isolates as a
    UserWarning and skips that ONE plugin; it never aborts the whole load.

    ``name`` here is always the entry-point name (the ``agent_runner.plugins``
    pyproject.toml key) — it's the identifier known BEFORE import, so it's what
    ``_admit_third_party`` gates on (pin lookup) and what ``doctor`` prints a
    checksum against. It is NOT necessarily ``PluginManifest.name`` (only known
    after import); for every builtin the two are pinned equal by
    ``test_entry_point_names_should_match_manifest_name``, but a third-party
    plugin has no such guarantee — which is fine, since admission never reads
    the manifest name at all.

    Builtin trust is decided by ``is_builtin_provenance(name, module_path)`` —
    a reserved name AND a module genuinely under
    ``agent_runner.builtin_plugins``. A reserved name alone is NOT enough: a
    name-squatter (reserved name, foreign module) is treated as third-party,
    flows through ``_admit_third_party``, and raises a loud
    ``plugin_builtin_name_squat`` signal. Only genuine builtins skip the gate.

    Every non-builtin entry passes through ``_admit_third_party`` BEFORE import
    — a pin mismatch or an unpinned plugin under ``sandbox = "require"`` refuses
    to import that ONE plugin (fail-closed; never loads unconfined).
    """
    import importlib
    import warnings

    from agent_runner._plugin_manifest import loaded_manifest_names, register_manifest
    from agent_runner._registry import BUILTIN_PLUGIN_NAMES, is_builtin_provenance

    disable = set(plugins_cfg.disable)
    already = set(loaded_manifest_names())
    for name, value in _DISCOVERED_PLUGIN_ENTRIES:
        if name in disable or name in already:
            continue
        # An entry-point value may carry a trailing extras marker
        # (``module:attr [extra1,extra2]``); importlib.metadata.EntryPoint's
        # own module/attr grammar excludes "[", so anything from the first
        # "[" onward is always extras, never part of the path — strip it
        # before resolving, or it glues onto attr_path and breaks getattr().
        module_path = _entry_point_module_path(value)
        attr_path = value.partition("[")[0].rstrip().partition(":")[2]
        # Builtin trust is keyed on PROVENANCE (module genuinely under
        # agent_runner.builtin_plugins), never on the entry-point NAME alone —
        # a reserved name is collidable, so a third-party package shipping an
        # entry named e.g. "pi" is a name-squatter, not a builtin.
        is_builtin = is_builtin_provenance(name, module_path)
        if not is_builtin:
            if name in BUILTIN_PLUGIN_NAMES:
                _warn_builtin_name_squat(name, module_path, log_dir)
            if not _admit_third_party(name, module_path, plugins_cfg, log_dir):
                continue
        try:
            mod = importlib.import_module(module_path)
            target = mod
            for attr in filter(None, attr_path.split(".")):
                target = getattr(target, attr)
            register_manifest(
                target, builtin=is_builtin, module_path=module_path, attr_path=attr_path
            )
        except Exception as e:  # noqa: BLE001 — a broken plugin must never crash the supervisor
            warnings.warn(f"failed to load {_PLUGIN_GROUP} plugin {name!r}: {e}", stacklevel=3)


def _warn_builtin_name_squat(name: str, module_path: str, log_dir) -> None:
    """A discovered entry point claims a RESERVED builtin name while resolving
    to a non-core module — a name-squatter. Builtin trust is denied (the entry
    still flows through ``_admit_third_party``'s pin/sandbox gate); this makes
    the claim LOUD (a warning always, plus an auditable event when a log_dir is
    available) rather than a silent trust bypass."""
    import warnings

    warnings.warn(
        f"{_PLUGIN_GROUP} entry {name!r} claims a reserved builtin name but resolves to "
        f"non-core module {module_path!r}; treating as third-party (pin/sandbox gate applies)",
        stacklevel=4,
    )
    if log_dir is not None:
        from agent_runner.api import emit_plugin_builtin_name_squat

        emit_plugin_builtin_name_squat(log_dir, name=name, module_path=module_path)


def _admit_third_party(name: str, module_path: str, plugins_cfg, log_dir) -> bool:
    """Fail-closed pin/sandbox gate for a non-builtin plugin (any entry lacking
    genuine builtin provenance, INCLUDING a reserved-name squatter). Returns
    False to refuse (and emit) — skipping this ONE plugin; every other
    discovered entry still loads.

    Both ``name`` and ``pins``' keys are entry-point names — see
    ``load_and_register_plugins``'s docstring. Runs BEFORE
    ``importlib.import_module``, so no plugin code executes for a refused
    entry.
    """
    from agent_runner._plugin_checksum import verify_pin
    from agent_runner._sandbox_probe import probe_sandbox_capability
    from agent_runner.api import emit_plugin_checksum_mismatch, emit_plugin_sandbox_degraded

    try:
        verdict, actual = verify_pin(name, module_path, plugins_cfg.pin)
    except Exception:  # noqa: BLE001 — unresolvable/unreadable module == refuse (fail closed)
        verdict, actual = "mismatch", "sha256:unreadable"
    if verdict == "mismatch":
        if log_dir is not None:
            emit_plugin_checksum_mismatch(
                log_dir,
                name=name,
                expected=plugins_cfg.pin.get(name, ""),
                actual=actual or "sha256:unreadable",
            )
        return False
    if verdict == "unpinned" and plugins_cfg.sandbox == "require":
        if log_dir is not None:
            probe = probe_sandbox_capability()
            emit_plugin_sandbox_degraded(
                log_dir,
                requested="require",
                achieved_tier=probe.achieved_tier,
                reason=f"{name}: unpinned third-party plugin under sandbox=require",
            )
        return False
    return True


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
