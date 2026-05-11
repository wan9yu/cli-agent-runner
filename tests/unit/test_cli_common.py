from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent_runner.cli.common import work_dir_from_args


def test_given_config_arg_when_resolved_then_returns_parent_dir(tmp_path: Path) -> None:
    cfg = tmp_path / "agent-runner.toml"
    cfg.write_text("")
    args = argparse.Namespace(config=cfg)
    assert work_dir_from_args(args) == tmp_path.resolve()


def test_given_no_config_attr_when_resolved_then_returns_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace()
    assert work_dir_from_args(args) == tmp_path.resolve()


def test_given_relative_default_config_path_when_resolved_then_returns_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(config=Path("./agent-runner.toml"))
    assert work_dir_from_args(args) == tmp_path.resolve()


def test_given_config_with_wrong_filename_when_resolved_then_raises(
    tmp_path: Path,
) -> None:
    args = argparse.Namespace(config=tmp_path / "custom-name.toml")
    with pytest.raises(ValueError, match="agent-runner.toml"):
        work_dir_from_args(args)
