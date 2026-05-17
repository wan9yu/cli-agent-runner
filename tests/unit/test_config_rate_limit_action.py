"""Unit tests for runtime.rate_limit_action config field."""

from __future__ import annotations

import pytest

from tests._test_helpers import make_toml_with_sections


@pytest.mark.parametrize("value", ["back_off", "skip", "stop"])
def test_given_valid_rate_limit_action_when_loaded_then_parses(tmp_path, value):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path, runtime_extra=f'rate_limit_action = "{value}"\n')
    cfg = load_config(cfg_path)
    assert cfg.runtime.rate_limit_action == value


def test_given_no_rate_limit_action_when_loaded_then_defaults_to_back_off(tmp_path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.runtime.rate_limit_action == "back_off"


def test_given_invalid_rate_limit_action_when_loaded_then_raises(tmp_path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path, runtime_extra='rate_limit_action = "explode"\n')
    # rate_limit_action is now an alias for transient_error_action; validation uses the new name
    with pytest.raises(ValueError, match=r"runtime\.transient_error_action.*explode"):
        load_config(cfg_path)
