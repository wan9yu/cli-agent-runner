"""Invariants for _BUILTIN_KINDS single-source-of-truth via reflection."""

from __future__ import annotations


def test_every_string_constant_is_a_builtin_kind():
    """Every UPPER_CASE = snake_case_value constant must be in _BUILTIN_KINDS.
    Prevents: define constant, forget to register.
    """
    import agent_runner.events as ev

    string_constants = {
        v
        for k, v in vars(ev).items()
        if k.isupper()
        and isinstance(v, str)
        and v.islower()
        and not v.startswith("_")
        and v.replace("_", "").isalnum()
    }
    missing = string_constants - ev._BUILTIN_KINDS
    assert not missing, f"constants not registered: {missing}"


def test_every_builtin_kind_has_a_constant():
    """Every entry in _BUILTIN_KINDS must correspond to a module-level constant.
    Prevents: hand-add string to set without constant; orphan stale strings.
    """
    import agent_runner.events as ev

    constant_values = {
        v
        for k, v in vars(ev).items()
        if k.isupper()
        and isinstance(v, str)
        and v.islower()
        and not v.startswith("_")
        and v.replace("_", "").isalnum()
    }
    orphans = ev._BUILTIN_KINDS - constant_values
    assert not orphans, f"_BUILTIN_KINDS members without constants: {orphans}"
