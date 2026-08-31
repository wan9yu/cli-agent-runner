from __future__ import annotations

from pathlib import Path

from agent_runner.api import PERMANENT_CONFIG_EXIT
from agent_runner.cli import main


def _broken_toml(tmp_path: Path) -> Path:
    # [prompt] with neither file nor files → ConfigError at load_config.
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
    )
    return toml


def test_given_broken_config_when_main_serve_then_exit_78_not_traceback(tmp_path: Path) -> None:
    rc = main(["serve", "--config", str(_broken_toml(tmp_path))])
    assert rc == PERMANENT_CONFIG_EXIT


def test_given_broken_config_when_main_then_names_migrate(tmp_path: Path, capsys) -> None:
    main(["serve", "--config", str(_broken_toml(tmp_path))])
    assert "agent-runner migrate" in capsys.readouterr().err
