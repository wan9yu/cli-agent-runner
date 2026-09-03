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


def test_given_missing_config_when_main_serve_then_exit_78_not_traceback(tmp_path: Path) -> None:
    # Group A: a single bad load (missing file, never even started the loop) is
    # PERMANENT -- fatal to serve immediately, not a 5-restart crash loop.
    rc = main(["serve", "--config", str(tmp_path / "nope.toml")])
    assert rc == PERMANENT_CONFIG_EXIT


def test_given_syntax_broken_toml_when_main_round_then_exit_78_not_traceback(
    tmp_path: Path,
) -> None:
    bad_toml = tmp_path / "agent-runner.toml"
    bad_toml.write_text("this is not [valid toml")
    rc = main(["round", "--config", str(bad_toml)])
    assert rc == PERMANENT_CONFIG_EXIT


def test_given_unreadable_config_when_main_serve_then_exit_78_not_traceback(
    tmp_path: Path,
) -> None:
    # chmod-000: the file exists (passes load_config's own exists() check) but
    # open() raises PermissionError -- an OSError subclass, not FileNotFoundError
    # or TOMLDecodeError, so it used to escape cfg_from_args_or_config_error's
    # narrower catch as a raw traceback instead of PERMANENT_CONFIG_EXIT.
    toml = _broken_toml(tmp_path)
    toml.chmod(0o000)
    try:
        rc = main(["serve", "--config", str(toml)])
    finally:
        toml.chmod(0o644)  # tmp_path teardown needs read/write back
    assert rc == PERMANENT_CONFIG_EXIT
