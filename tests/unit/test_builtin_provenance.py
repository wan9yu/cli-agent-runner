"""is_builtin_provenance is the single source of truth for builtin trust: a
reserved name is necessary but NOT sufficient -- the module must genuinely live
under agent_runner.builtin_plugins, which third-party code cannot forge. These
tests pin the three cells of that truth table that matter for the trust
boundary."""

from __future__ import annotations

from agent_runner._registry import BUILTIN_PLUGIN_NAMES, is_builtin_provenance


def test_is_builtin_provenance_should_return_true_when_reserved_name_and_core_module() -> None:
    name = next(iter(BUILTIN_PLUGIN_NAMES))

    result = is_builtin_provenance(name, f"agent_runner.builtin_plugins.{name}")

    assert result is True


def test_is_builtin_provenance_should_return_false_when_reserved_name_but_foreign_module() -> None:
    name = next(iter(BUILTIN_PLUGIN_NAMES))

    result = is_builtin_provenance(name, f"evil_pkg.{name}")

    assert result is False


def test_is_builtin_provenance_should_return_false_when_core_module_but_name_not_reserved() -> None:
    result = is_builtin_provenance("acme_unreserved", "agent_runner.builtin_plugins.acme")

    assert result is False
