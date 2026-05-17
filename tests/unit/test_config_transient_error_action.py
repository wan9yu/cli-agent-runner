"""Unit tests for runtime.transient_error_action config field and rate_limit_action alias."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from tests._test_helpers import make_toml_with_sections


@pytest.mark.parametrize("value", ["back_off", "skip", "stop"])
def test_given_valid_transient_error_action_when_loaded_then_parses(tmp_path: Path, value: str):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra=f'transient_error_action = "{value}"\n'
    )
    cfg = load_config(cfg_path)
    assert cfg.runtime.transient_error_action == value


def test_given_no_transient_error_action_when_loaded_then_defaults_to_back_off(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.runtime.transient_error_action == "back_off"


def test_given_invalid_transient_error_action_when_loaded_then_raises(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra='transient_error_action = "explode"\n'
    )
    with pytest.raises(ValueError, match=r"runtime\.transient_error_action.*explode"):
        load_config(cfg_path)


def test_given_rate_limit_action_alias_when_loaded_then_warns_and_aliases(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path, runtime_extra='rate_limit_action = "stop"\n')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_config(cfg_path)
    deps = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning) and "rate_limit_action" in str(w.message)
    ]
    assert deps, f"expected DeprecationWarning, got: {[str(w.message) for w in caught]}"
    assert cfg.runtime.transient_error_action == "stop"


def test_given_both_actions_set_when_loaded_then_raises(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra=('rate_limit_action = "back_off"\ntransient_error_action = "back_off"\n'),
    )
    with pytest.raises(
        ValueError,
        match=r"set either runtime\.transient_error_action or runtime\.rate_limit_action",
    ):
        load_config(cfg_path)
