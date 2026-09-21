"""Invariants for transient-error classification SSOT."""

from __future__ import annotations


def test_back_off_defaults_keys_should_be_subset_of_classifications_when_invoked():

    from agent_runner.builtin_plugins._constants import _BACK_OFF_DEFAULTS, _CLASSIFICATIONS

    actual = set(_BACK_OFF_DEFAULTS.keys())

    extra = actual - _CLASSIFICATIONS
    assert not extra, f"_BACK_OFF_DEFAULTS keys not all in _CLASSIFICATIONS: {extra}"


def test_back_off_defaults_plus_account_should_equal_classifications_when_invoked():
    """Every classification has either a default back-off OR server-provided reset
    (rate_limit_account uses Anthropic resetsAt, others use _BACK_OFF_DEFAULTS).
    """
    from agent_runner.builtin_plugins._constants import _BACK_OFF_DEFAULTS, _CLASSIFICATIONS

    actual = set(_BACK_OFF_DEFAULTS.keys()) | {"rate_limit_account"}

    assert actual == _CLASSIFICATIONS
