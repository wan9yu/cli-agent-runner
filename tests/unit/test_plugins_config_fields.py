from __future__ import annotations

import re

import pytest

from agent_runner.config.errors import ConfigError
from agent_runner.config.parsers import _parse_plugins


def test_parse_plugins_should_default_disable_empty_when_unset():
    cfg = _parse_plugins({})

    actual = cfg.disable

    assert actual == []


def test_parse_plugins_should_carry_disable_when_present():
    cfg = _parse_plugins({"disable": ["acme_plugin"]})

    actual = cfg.disable

    assert actual == ["acme_plugin"]


def test_parse_plugins_should_raise_when_unknown_key_present():
    raw = {"acme_setting": 5}

    with pytest.raises(ConfigError, match="unknown \\[plugins\\] field.*acme_setting") as caught:
        _parse_plugins(raw)

    assert re.search(r"unknown \[plugins\] field.*acme_setting", str(caught.value))
