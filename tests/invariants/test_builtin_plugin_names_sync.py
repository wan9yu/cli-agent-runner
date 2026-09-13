"""BUILTIN_PLUGIN_NAMES is the one place trust-by-identity is decided (the
trampoline routes anything NOT in it through the sandbox). This locks it to
pyproject's entry-point table so a new builtin plugin can never silently be
treated as third-party (or vice versa) by a drift between the two lists."""

from __future__ import annotations

import tomllib
from pathlib import Path

from agent_runner._registry import BUILTIN_PLUGIN_NAMES

_PYPROJECT = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"


def test_builtin_plugin_names_should_match_pyproject_entry_points() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))

    declared = set(data["project"]["entry-points"]["agent_runner.plugins"])

    assert BUILTIN_PLUGIN_NAMES == frozenset(declared)
