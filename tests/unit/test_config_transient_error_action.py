"""Unit tests for runtime.transient_error_action config field."""

from __future__ import annotations

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
