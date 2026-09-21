"""0.2.12 config strictness (BREAKING): bare-string list rejection, empty
command / top-level files rejection, unknown [schedule]/[prompt] keys,
threshold<=window. Per-phase prompt.files = [] stays a valid distinct state."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_runner.config import ConfigError, load_config
from tests._test_helpers import PRESET_NAMES


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent-runner.toml"
    p.write_text(body, encoding="utf-8")
    return p


def _base(wd: Path, extra: str = "", *, command: str = '["true"]') -> str:
    return (
        "schema_version = 1\n"
        "[agent]\n"
        f"command = {command}\n"
        'prompt_arg_template = ["-p", "{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{wd}"\n'
        f'log_dir = "{wd}/logs"\n'
        "[prompt]\n"
        f'file = "{wd}/prompt.md"\n'
    ) + extra


def test_bare_string_command_should_be_rejected_when_invoked(tmp_path: Path) -> None:
    p = _write(tmp_path, _base(tmp_path, command='"claude"'))

    with pytest.raises(ConfigError, match="must be a list") as e:
        load_config(p)

    assert "agent-runner migrate" in str(e.value)


def test_empty_command_should_be_rejected_when_invoked(tmp_path: Path) -> None:

    p = _write(tmp_path, _base(tmp_path, command="[]"))

    with pytest.raises(ConfigError, match="non-empty") as caught:
        load_config(p)

    assert re.search(r"non-empty", str(caught.value))


def test_bare_string_phases_list_should_be_rejected_when_invoked(tmp_path: Path) -> None:

    p = _write(tmp_path, _base(tmp_path, '[phases]\nlist = "dev"\n'))

    with pytest.raises(ConfigError, match="must be a list") as caught:
        load_config(p)

    assert re.search(r"must be a list", str(caught.value))


def test_empty_top_level_prompt_files_should_be_rejected_when_invoked(tmp_path: Path) -> None:

    body = (
        "schema_version = 1\n"
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["-p", "{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        "[prompt]\nfiles = []\n"
    )

    with pytest.raises(ConfigError, match="non-empty") as caught:
        load_config(_write(tmp_path, body))

    assert re.search(r"non-empty", str(caught.value))


def test_per_phase_prompt_files_empty_should_be_preserved_when_invoked(tmp_path: Path) -> None:
    extra = '[phases]\nlist = ["dev"]\n[phases.dev.prompt]\nfiles = []\n'

    cfg = load_config(_write(tmp_path, _base(tmp_path, extra)))

    assert cfg.profile_for("dev").prompt_files == []  # distinct from None


def test_unknown_schedule_key_should_be_rejected_when_invoked(tmp_path: Path) -> None:

    p = _write(tmp_path, _base(tmp_path, "[schedule]\nbogus = 1\n"))

    with pytest.raises(ConfigError, match=r"unknown \[schedule\]") as caught:
        load_config(p)

    assert re.search(r"unknown \[schedule\]", str(caught.value))


def test_unknown_prompt_key_should_be_rejected_when_invoked(tmp_path: Path) -> None:

    body = (
        "schema_version = 1\n"
        '[agent]\ncommand = ["true"]\nprompt_arg_template = ["-p", "{prompt}"]\n'
        f'[runtime]\nwork_dir = "{tmp_path}"\nlog_dir = "{tmp_path}/logs"\n'
        f'[prompt]\nfile = "{tmp_path}/p.md"\nbogus = 1\n'
    )

    with pytest.raises(ConfigError, match=r"unknown \[prompt\]") as caught:
        load_config(_write(tmp_path, body))

    assert re.search(r"unknown \[prompt\]", str(caught.value))


def test_threshold_greater_than_window_should_be_rejected_when_invoked(tmp_path: Path) -> None:

    extra = "[monitor]\nanomaly_repetitive_window = 3\nanomaly_repetitive_threshold = 5\n"

    with pytest.raises(ConfigError, match="anomaly_repetitive_threshold") as caught:
        load_config(_write(tmp_path, _base(tmp_path, extra)))

    assert re.search(r"anomaly_repetitive_threshold", str(caught.value))


@pytest.mark.parametrize("preset", PRESET_NAMES)
def test_shipped_preset_should_load_under_strictness_when_invoked(
    tmp_git_repo: Path, preset: str
) -> None:
    from agent_runner.api import init

    init(tmp_git_repo, preset=preset, commit=False)

    load_config(tmp_git_repo / "agent-runner.toml")  # must not raise
