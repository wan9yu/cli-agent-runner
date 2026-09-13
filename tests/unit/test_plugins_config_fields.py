from __future__ import annotations

import pytest

from agent_runner.config.errors import ConfigError
from agent_runner.config.parsers import _parse_plugins


def test_parse_plugins_should_default_sandbox_prefer_when_unset():
    cfg = _parse_plugins({})

    assert cfg.sandbox == "prefer"
    assert cfg.spawn_override_allow == []
    assert cfg.pin == {}


def test_parse_plugins_should_carry_new_fields_when_present():
    cfg = _parse_plugins(
        {
            "sandbox": "require",
            "spawn_override_allow": ["maintenance_gate"],
            "pin": {"acme_dirty": "sha256:abc"},
        }
    )

    assert cfg.sandbox == "require"
    assert cfg.spawn_override_allow == ["maintenance_gate"]
    assert cfg.pin == {"acme_dirty": "sha256:abc"}


def test_parse_plugins_should_raise_when_sandbox_mode_unknown():
    with pytest.raises(ConfigError, match="plugins.sandbox"):
        _parse_plugins({"sandbox": "loose"})


def test_parse_plugins_should_raise_when_pin_not_a_table():
    with pytest.raises(ConfigError, match=r"\[plugins.pin\]"):
        _parse_plugins({"pin": ["not", "a", "table"]})


def test_parse_plugins_should_raise_when_pin_value_not_a_string():
    with pytest.raises(ConfigError, match=r"\[plugins\.pin\]"):
        _parse_plugins({"pin": {"acme_dirty": 123}})


def test_parse_plugins_should_raise_when_spawn_override_allow_not_a_list():
    with pytest.raises(ConfigError, match="plugins.spawn_override_allow"):
        _parse_plugins({"spawn_override_allow": {"a": 1}})


def test_parse_plugins_should_raise_when_spawn_override_allow_has_bool_element():
    with pytest.raises(ConfigError, match="plugins.spawn_override_allow"):
        _parse_plugins({"spawn_override_allow": [True]})


def test_parse_plugins_should_leave_unknown_keys_in_raw_when_present():
    cfg = _parse_plugins({"sandbox": "off", "acme_setting": 5})

    assert cfg.raw == {"acme_setting": 5}
