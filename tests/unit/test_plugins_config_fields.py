from __future__ import annotations

import pytest

from agent_runner.config.errors import ConfigError
from agent_runner.config.parsers import _parse_plugins


def test_parse_plugins_should_default_disable_empty_when_unset():
    cfg = _parse_plugins({})

    assert cfg.disable == []


def test_parse_plugins_should_carry_disable_when_present():
    cfg = _parse_plugins({"disable": ["acme_plugin"]})

    assert cfg.disable == ["acme_plugin"]


def test_parse_plugins_should_raise_when_unknown_key_present():
    with pytest.raises(ConfigError, match=r"unknown \[plugins\] field.*acme_setting"):
        _parse_plugins({"acme_setting": 5})
