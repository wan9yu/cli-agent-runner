"""Unit tests for RuntimeConfig.fresh_eyes_every_n field."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import make_toml_with_sections


def test_given_fresh_eyes_every_n_50_when_loaded_then_field_set(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path, runtime_extra="fresh_eyes_every_n = 50\n")
    cfg = load_config(cfg_path)
    assert cfg.runtime.fresh_eyes_every_n == 50


def test_given_no_fresh_eyes_when_loaded_then_defaults_to_none(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.runtime.fresh_eyes_every_n is None


@pytest.mark.parametrize("invalid", [0, -1, -100])
def test_given_invalid_fresh_eyes_when_loaded_then_raises(tmp_path: Path, invalid: int):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path, runtime_extra=f"fresh_eyes_every_n = {invalid}\n")
    with pytest.raises(ValueError, match=r"runtime\.fresh_eyes_every_n"):
        load_config(cfg_path)
