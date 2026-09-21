from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_runner.api import PERMANENT_CONFIG_EXIT
from agent_runner.cli import main
from tests._test_helpers import make_toml_with_sections


def _broken_toml(tmp_path: Path) -> Path:
    # [prompt] with neither file nor files → ConfigError at load_config.
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "schema_version = 1\n"
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
    )
    return toml


def test_main_serve_should_exit_78_not_traceback_when_config_broken(tmp_path: Path) -> None:

    rc = main(["serve", "--config", str(_broken_toml(tmp_path))])

    actual = rc

    assert actual == PERMANENT_CONFIG_EXIT


def test_main_should_name_migrate_in_stderr_when_config_broken(tmp_path: Path, capsys) -> None:
    main(["serve", "--config", str(_broken_toml(tmp_path))])

    err = capsys.readouterr().err
    # Pins the [prompt]-validation path specifically -- not just any
    # ConfigError -- so this can't silently go vacuous against some
    # unrelated, earlier-firing config rejection (e.g. the schema_version
    # boot gate) that also happens to mention "agent-runner migrate".
    assert "missing required field: prompt.file or prompt.files" in err

    assert "agent-runner migrate" in err


def test_main_serve_should_exit_78_not_traceback_when_config_missing(tmp_path: Path) -> None:
    # Group A: a single bad load (missing file, never even started the loop) is
    # PERMANENT -- fatal to serve immediately, not a 5-restart crash loop.

    rc = main(["serve", "--config", str(tmp_path / "nope.toml")])

    assert rc == PERMANENT_CONFIG_EXIT


def test_main_round_should_exit_78_not_traceback_when_toml_syntax_broken(
    tmp_path: Path,
) -> None:
    bad_toml = tmp_path / "agent-runner.toml"
    bad_toml.write_text("this is not [valid toml")

    rc = main(["round", "--config", str(bad_toml)])

    assert rc == PERMANENT_CONFIG_EXIT


def test_main_serve_should_not_name_migrate_in_stderr_when_phase_windows_overlap(
    tmp_path: Path,
) -> None:
    # The phase-window-overlap ConfigError already names its OWN remedy
    # ("run `agent-runner doctor`") -- migrate cannot carve out an overlap, so
    # main()'s generic migrate suffix must not also be appended (which would
    # contradict the error's own advice).
    phases_block = (
        '[phases]\nlist = ["a", "b"]\n'
        '[phases.a.agent]\nname = "agent-a"\n'
        '[phases.a.schedule]\ntimezone = "UTC"\nrun_windows = ["09:00-12:00"]\n'
        '[phases.b.agent]\nname = "agent-b"\n'
        '[phases.b.schedule]\ntimezone = "UTC"\nrun_windows = ["11:00-14:00"]\n'
    )
    toml = make_toml_with_sections(tmp_path, phases_block=phases_block)

    rc = main(["serve", "--config", str(toml)])

    assert rc == PERMANENT_CONFIG_EXIT


def test_main_serve_stderr_should_omit_migrate_suffix_when_phase_windows_overlap(
    tmp_path: Path, capsys
) -> None:
    phases_block = (
        '[phases]\nlist = ["a", "b"]\n'
        '[phases.a.agent]\nname = "agent-a"\n'
        '[phases.a.schedule]\ntimezone = "UTC"\nrun_windows = ["09:00-12:00"]\n'
        '[phases.b.agent]\nname = "agent-b"\n'
        '[phases.b.schedule]\ntimezone = "UTC"\nrun_windows = ["11:00-14:00"]\n'
    )
    toml = make_toml_with_sections(tmp_path, phases_block=phases_block)

    main(["serve", "--config", str(toml)])

    err = capsys.readouterr().err
    assert "phase-window overlap" in err
    assert "agent-runner doctor" in err
    assert "Run `agent-runner migrate` then retry." not in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file perms")
def test_main_serve_should_exit_78_not_traceback_when_config_unreadable(
    tmp_path: Path,
) -> None:
    # Under euid 0 this asserts VACUOUSLY: root can read a 000 file, so open()
    # succeeds and the path taken is TOMLDecodeError, not the PermissionError
    # this test exists to cover -- skip so it fails loudly only when it can.
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
