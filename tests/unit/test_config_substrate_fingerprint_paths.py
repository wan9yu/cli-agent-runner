"""Unit tests for RuntimeConfig.substrate_fingerprint_paths field."""

from __future__ import annotations

from pathlib import Path

from tests._test_helpers import make_toml_with_sections


def test_given_paths_list_when_loaded_then_field_set(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(
        tmp_path,
        runtime_extra='substrate_fingerprint_paths = ["*.py", "specs/*.md"]\n',
    )
    cfg = load_config(cfg_path)
    assert cfg.runtime.substrate_fingerprint_paths == ["*.py", "specs/*.md"]


def test_given_no_paths_when_loaded_then_defaults_empty(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.runtime.substrate_fingerprint_paths == []
