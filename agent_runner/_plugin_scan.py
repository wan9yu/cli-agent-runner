"""Plugin discovery scanner — replaces per-group importlib.metadata.entry_points
scans at startup with one cheap entry_points.txt parse. Hard-falls-back to
importlib.metadata on any parse failure; AGENT_RUNNER_PLUGIN_DISCOVERY=metadata
forces the fallback. Parity with importlib.metadata is pinned by
tests/unit/test_plugin_scan_parity.py per group."""

from __future__ import annotations

import configparser
import os
import warnings
from pathlib import Path


def _metadata_entry_points(group: str) -> list[tuple[str, str]]:
    """The old path: importlib.metadata's own distribution scan.

    Imported lazily — importlib.metadata pulls in email.* (METADATA parsing)
    at import time, which is most of the footprint this scanner exists to
    avoid. Importing it at module top would tax every process even when the
    fast path never falls back.
    """
    from importlib.metadata import entry_points

    return [(ep.name, ep.value) for ep in entry_points(group=group)]


# Parsed entry_points.txt contents, memoized per dist-info file for the life
# of the process: {ep_file_path: {group: [(name, value), ...]}}. Package
# import calls _parse_entry_points_files once per plugin group (7x today,
# _HOOK_GROUPS + event_kinds + detectors) across the SAME sys.path, so every
# call after the first re-walks the identical dist-info directories --
# without this cache each one would re-open and re-parse (configparser) every
# entry_points.txt found, 7x over, just to pull out a different [group]
# section each time. Keyed by the file's path only (not mtime) -- a dist-info
# is not expected to change under a running process.
_PARSED_DIST_INFO_CACHE: dict[Path, dict[str, list[tuple[str, str]]]] = {}


def _parsed_entry_points_by_group(ep_file: Path) -> dict[str, list[tuple[str, str]]]:
    """One ``entry_points.txt``, parsed once and bucketed by ``[group]``
    section: ``{group: [(name, value), ...]}``. Memoized in
    :data:`_PARSED_DIST_INFO_CACHE` so a second call for the same file (a
    different plugin group asking about the same dist-info) reuses the
    parse instead of re-opening + re-running configparser over it."""
    cached = _PARSED_DIST_INFO_CACHE.get(ep_file)
    if cached is not None:
        return cached
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # preserve case -- entry-point names are case-sensitive
    parser.read(ep_file, encoding="utf-8")
    parsed = {section: list(parser[section].items()) for section in parser.sections()}
    _PARSED_DIST_INFO_CACHE[ep_file] = parsed
    return parsed


def _parse_entry_points_files(sys_path: list[str], group: str) -> list[tuple[str, str]]:
    """(name, value) pairs for ``group`` by parsing each ``*.dist-info/entry_points.txt``
    found on ``sys_path``. A name seen in an earlier ``sys.path`` entry wins over a
    later one — the same first-found precedence ``sys.path`` gives real imports.

    A dropped duplicate emits a ``UserWarning`` naming the group, the duplicated
    name, and which value won — the same diagnostic value the old
    ``importlib.metadata`` + ``ensure_unique`` path gave operators (that path
    let both entries load and had ``ensure_unique`` reject the second with a
    warning); silently dropping it here would regress that visibility.
    """
    out: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for entry in sys_path:
        base = Path(entry) if entry else Path.cwd()
        if not base.is_dir():
            continue
        for dist_info in base.glob("*.dist-info"):
            ep_file = dist_info / "entry_points.txt"
            if not ep_file.is_file():
                continue
            groups = _parsed_entry_points_by_group(ep_file)
            if group not in groups:
                continue
            for name, value in groups[group]:
                winner = seen.get(name)
                if winner is None:  # dedup: first sys.path entry wins
                    seen[name] = value
                    out.append((name, value))
                elif winner != value:
                    warnings.warn(
                        f"duplicate entry_point {name!r} in group {group!r}: "
                        f"{value!r} dropped in favor of the earlier {winner!r} "
                        f"(first sys.path entry wins)",
                        stacklevel=2,
                    )
    return out


def scan_entry_points(sys_path: list[str], group: str) -> list[tuple[str, str]]:
    """(name, 'module:attr') pairs for ``group`` across ``sys_path``.

    Hard-falls-back to ``importlib.metadata.entry_points`` on any parse
    failure, or unconditionally when ``AGENT_RUNNER_PLUGIN_DISCOVERY=metadata``
    is set (escape hatch for an environment where the scan and metadata
    disagree).
    """
    if os.environ.get("AGENT_RUNNER_PLUGIN_DISCOVERY") == "metadata":
        return _metadata_entry_points(group)
    try:
        return _parse_entry_points_files(sys_path, group)
    except Exception:  # noqa: BLE001 — never let discovery crash import; fall back
        return _metadata_entry_points(group)
