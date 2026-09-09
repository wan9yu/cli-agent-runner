from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.config import load_config
from tests._test_helpers import make_toml_with_sections


def test_max_rounds_should_be_set_when_configured(tmp_path: Path):
    cfg_path = make_toml_with_sections(tmp_path, runtime_extra="max_rounds = 3\n")

    cfg = load_config(cfg_path)

    assert cfg.runtime.max_rounds == 3


def test_max_rounds_should_default_to_none_when_not_configured(tmp_path: Path):
    cfg_path = make_toml_with_sections(tmp_path)

    cfg = load_config(cfg_path)

    assert cfg.runtime.max_rounds is None


@pytest.mark.parametrize("invalid", [0, -1, -100])
def test_load_config_should_raise_when_max_rounds_is_invalid(tmp_path: Path, invalid: int):
    cfg_path = make_toml_with_sections(tmp_path, runtime_extra=f"max_rounds = {invalid}\n")

    with pytest.raises(ValueError, match=r"runtime\.max_rounds"):
        load_config(cfg_path)
