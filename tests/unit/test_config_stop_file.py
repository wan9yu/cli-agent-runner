from __future__ import annotations

from pathlib import Path

from agent_runner.config import load_config
from tests._test_helpers import make_toml_with_sections


def test_relative_stop_file_should_resolve_against_work_dir_when_loaded(tmp_path: Path):
    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra='stop_file = "logs/stop-requested"\n'
    )

    cfg = load_config(cfg_path)

    assert cfg.runtime.stop_file is not None
    assert cfg.runtime.stop_file.is_absolute()
    assert cfg.runtime.stop_file.name == "stop-requested"
    # resolves against work_dir
    assert str(tmp_path) in str(cfg.runtime.stop_file)


def test_stop_file_should_default_to_none_when_not_configured(tmp_path: Path):
    cfg_path = make_toml_with_sections(tmp_path)

    cfg = load_config(cfg_path)

    assert cfg.runtime.stop_file is None


def test_absolute_stop_file_should_be_unchanged_when_loaded(tmp_path: Path):
    abs_path = "/tmp/agent-runner-stop-fixed"
    cfg_path = make_toml_with_sections(tmp_path, runtime_extra=f'stop_file = "{abs_path}"\n')

    cfg = load_config(cfg_path)

    assert cfg.runtime.stop_file == Path(abs_path)
