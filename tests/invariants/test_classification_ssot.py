"""Invariants for transient-error classification SSOT."""

from __future__ import annotations


def test_back_off_defaults_keys_are_subset_of_classifications():
    from agent_runner.builtin_plugins._constants import _BACK_OFF_DEFAULTS, _CLASSIFICATIONS

    assert set(_BACK_OFF_DEFAULTS.keys()) <= _CLASSIFICATIONS, (
        f"_BACK_OFF_DEFAULTS keys not all in _CLASSIFICATIONS: "
        f"{set(_BACK_OFF_DEFAULTS.keys()) - _CLASSIFICATIONS}"
    )


def test_back_off_defaults_plus_account_equals_classifications():
    """Every classification has either a default back-off OR server-provided reset
    (rate_limit_account uses Anthropic resetsAt, others use _BACK_OFF_DEFAULTS).
    """
    from agent_runner.builtin_plugins._constants import _BACK_OFF_DEFAULTS, _CLASSIFICATIONS

    assert set(_BACK_OFF_DEFAULTS.keys()) | {"rate_limit_account"} == _CLASSIFICATIONS
