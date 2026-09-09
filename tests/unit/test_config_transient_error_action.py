"""Unit tests for runtime.transient_error_action config field."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import load_config
from tests._test_helpers import make_toml_with_sections


@pytest.mark.parametrize("value", ["back_off", "skip", "stop"])
def test_transient_error_action_should_parse_when_value_is_valid(tmp_path: Path, value: str):
    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra=f'transient_error_action = "{value}"\n'
    )

    cfg = load_config(cfg_path)

    assert cfg.runtime.transient_error_action == value


def test_transient_error_action_should_default_to_back_off_when_not_configured(tmp_path: Path):
    cfg_path = make_toml_with_sections(tmp_path)

    cfg = load_config(cfg_path)

    assert cfg.runtime.transient_error_action == "back_off"


def test_load_config_should_raise_when_transient_error_action_is_invalid(tmp_path: Path):
    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra='transient_error_action = "explode"\n'
    )

    with pytest.raises(ValueError, match=r"runtime\.transient_error_action.*explode"):
        load_config(cfg_path)
