from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import make_toml_with_sections


def test_given_max_rounds_3_when_loaded_then_field_set(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra="max_rounds = 3\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.runtime.max_rounds == 3


def test_given_no_max_rounds_when_loaded_then_defaults_to_none(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.runtime.max_rounds is None


@pytest.mark.parametrize("invalid", [0, -1, -100])
def test_given_invalid_max_rounds_when_loaded_then_raises(tmp_path: Path, invalid: int):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra=f"max_rounds = {invalid}\n"
    )
    with pytest.raises(ValueError, match=r"runtime\.max_rounds"):
        load_config(cfg_path)
