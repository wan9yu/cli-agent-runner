from __future__ import annotations

from pathlib import Path

from tests._test_helpers import make_toml_with_sections


def test_given_relative_stop_file_when_loaded_then_resolves_against_work_dir(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra='stop_file = "logs/stop-requested"\n'
    )
    cfg = load_config(cfg_path)
    assert cfg.runtime.stop_file is not None
    assert cfg.runtime.stop_file.is_absolute()
    assert cfg.runtime.stop_file.name == "stop-requested"
    # resolves against work_dir
    assert str(tmp_path) in str(cfg.runtime.stop_file)


def test_given_no_stop_file_when_loaded_then_defaults_to_none(tmp_path: Path):
    from agent_runner.config import load_config

    cfg_path = make_toml_with_sections(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.runtime.stop_file is None


def test_given_absolute_stop_file_when_loaded_then_unchanged(tmp_path: Path):
    from agent_runner.config import load_config

    abs_path = "/tmp/agent-runner-stop-fixed"
    cfg_path = make_toml_with_sections(
        tmp_path, runtime_extra=f'stop_file = "{abs_path}"\n'
    )
    cfg = load_config(cfg_path)
    assert cfg.runtime.stop_file == Path(abs_path)
