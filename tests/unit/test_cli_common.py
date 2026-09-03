from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent_runner.cli.common import cfg_from_args, work_dir_from_args


def test_given_config_arg_when_resolved_then_returns_parent_dir(tmp_path: Path) -> None:
    cfg = tmp_path / "agent-runner.toml"
    cfg.write_text("")
    args = argparse.Namespace(config=cfg)
    assert work_dir_from_args(args) == tmp_path.resolve()


def test_given_no_config_attr_when_resolved_then_returns_cwd(tmp_path: Path, monkeypatch) -> None:
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


# ---------------------------------------------------------------------------
# cfg_from_args — routed through _resolve.config_path (single source for
# "given CLI args, which toml"), same as work_dir_from_args above.


def test_given_work_dir_differs_from_toml_dir_when_cfg_from_args_then_loads_declared_work_dir(
    tmp_path: Path,
) -> None:
    """config_path must reflect the --config path itself, not assume work_dir
    and the toml's own directory coincide -- cfg_from_args must load the SAME
    file work_dir_from_args/_resolve.config_path would resolve to."""
    toml_dir = tmp_path / "configs"
    toml_dir.mkdir()
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    (work_dir / "p.md").write_text("hi")
    cfg_path = toml_dir / "agent-runner.toml"
    cfg_path.write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        f"[runtime]\nwork_dir = '{work_dir}'\nlog_dir = 'logs'\n[prompt]\nfile = 'p.md'\n"
    )
    # A plain string, not a Path -- _resolve.config_path wraps it; cfg_from_args
    # calling load_config(args.config) directly would AttributeError on
    # toml_path.exists() since str has no such method.
    args = argparse.Namespace(config=str(cfg_path))

    cfg = cfg_from_args(args)

    assert cfg.runtime.work_dir == work_dir.resolve()


def test_given_symlinked_config_when_cfg_from_args_then_work_dir_anchors_to_symlink_dir(
    tmp_path: Path,
) -> None:
    """A relative [runtime] work_dir must anchor to the --config symlink's OWN
    directory, not the resolved target's directory -- the same .absolute()
    (never .resolve()) guarantee _resolve.config_path makes directly."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "p.md").write_text("hi")
    real_cfg = real_dir / "agent-runner.toml"
    real_cfg.write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        "[runtime]\nwork_dir = '.'\nlog_dir = 'logs'\n[prompt]\nfile = 'p.md'\n"
    )
    link_dir = tmp_path / "link"
    link_dir.mkdir()
    (link_dir / "p.md").write_text("hi")
    link_cfg = link_dir / "agent-runner.toml"
    link_cfg.symlink_to(real_cfg)

    cfg = cfg_from_args(argparse.Namespace(config=link_cfg))

    assert cfg.runtime.work_dir == link_dir.resolve()
