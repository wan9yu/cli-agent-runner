"""Unit tests for agent_runner._resolve — the internal identity/location
resolver (Group C, 0.2.13). INTERNAL ONLY: not a public contract.

Lenient/strict split under test (spec-review correction): strict=True is for
identity-interpolated sites (unit filenames / ssh / systemd); strict=False is
for descriptive uses (hook_ctx.project, scaffold) where a spaced/CJK work_dir
basename must keep working (no new break to dev-box paths).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runner import _resolve
from agent_runner.config import load_config
from agent_runner.service_unit import serve_unit_filename

# ---------------------------------------------------------------------------
# project_name — lenient/strict split


def test_project_name_should_accept_spaced_basename_when_lenient(tmp_path):
    work_dir = tmp_path / "my project"

    assert _resolve.project_name(work_dir, strict=False) == "my project"


def test_project_name_should_accept_cjk_basename_when_lenient(tmp_path):
    work_dir = tmp_path / "我的项目"

    assert _resolve.project_name(work_dir, strict=False) == "我的项目"


def test_project_name_should_reject_spaced_basename_when_strict(tmp_path):
    work_dir = tmp_path / "my project"

    with pytest.raises(ValueError, match="invalid project name"):
        _resolve.project_name(work_dir, strict=True)


def test_project_name_should_reject_cjk_basename_when_strict(tmp_path):
    work_dir = tmp_path / "我的项目"

    with pytest.raises(ValueError, match="invalid project name"):
        _resolve.project_name(work_dir, strict=True)


def test_project_name_should_accept_plain_basename_when_strict(tmp_path):
    work_dir = tmp_path / "my-project_v1.2"

    assert _resolve.project_name(work_dir, strict=True) == "my-project_v1.2"


def test_project_name_should_accept_plain_basename_when_lenient(tmp_path):
    work_dir = tmp_path / "my-project_v1.2"

    assert _resolve.project_name(work_dir, strict=False) == "my-project_v1.2"


# ---------------------------------------------------------------------------
# config_path — single source for "given CLI args, which toml"


def test_config_path_should_return_explicit_config_when_args_config_is_set(tmp_path):
    cfg_file = tmp_path / "agent-runner.toml"
    cfg_file.write_text("")
    args = SimpleNamespace(config=cfg_file)

    assert _resolve.config_path(args) == cfg_file.resolve()


def test_config_path_should_default_to_cwd_toml_when_config_arg_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace()

    assert _resolve.config_path(args) == tmp_path.resolve() / "agent-runner.toml"


def test_config_path_should_return_args_config_verbatim_when_work_dir_differs_from_toml_dir(
    tmp_path,
):
    """The seam this resolver closes: config_path must reflect the --config the
    caller gave, not assume work_dir and the toml's own directory coincide."""
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
    args = SimpleNamespace(config=cfg_path)

    resolved = _resolve.config_path(args)

    assert resolved == cfg_path.resolve()
    cfg = load_config(resolved)
    assert cfg.runtime.work_dir == work_dir.resolve()
    # config_path must NOT silently coerce to the config's declared work_dir
    assert resolved.parent != cfg.runtime.work_dir


def test_config_path_should_anchor_to_symlink_dir_not_target_when_config_is_symlinked(tmp_path):
    """A symlinked --config must resolve to a path anchored in the SYMLINK's own
    directory, not the resolved target's directory: config_path uses
    ``.absolute()`` (never dereferences symlinks), not ``.resolve()`` (which
    would silently follow the link) -- otherwise a relative [runtime] work_dir
    in the target toml would anchor to the wrong directory for a caller who
    only ever named the symlink."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_cfg = real_dir / "agent-runner.toml"
    real_cfg.write_text("")

    link_dir = tmp_path / "link"
    link_dir.mkdir()
    link_cfg = link_dir / "agent-runner.toml"
    link_cfg.symlink_to(real_cfg)

    args = SimpleNamespace(config=link_cfg)
    resolved = _resolve.config_path(args)

    assert resolved == link_cfg
    assert resolved.parent == link_dir


# ---------------------------------------------------------------------------
# unit_filename — thin wrap of the existing serve_unit_filename


def test_unit_filename_should_equal_serve_unit_filename_output():
    assert _resolve.unit_filename("myproj") == serve_unit_filename("myproj")
    assert _resolve.unit_filename("myproj") == "agent-runner@myproj.service"


# ---------------------------------------------------------------------------
# log_dir — reads from config when present, conventional fallback otherwise


def test_log_dir_should_read_from_config_when_toml_present(tmp_path):
    (tmp_path / "p.md").write_text("hi")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        "[runtime]\nwork_dir = '.'\nlog_dir = 'custom-logs'\n[prompt]\nfile = 'p.md'\n"
    )

    assert _resolve.log_dir(tmp_path) == (tmp_path / "custom-logs").resolve()


def test_log_dir_should_fall_back_to_conventional_path_when_toml_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    work_dir = tmp_path / "noconfig"
    work_dir.mkdir()

    assert _resolve.log_dir(work_dir) == tmp_path / ".agent-runner" / "noconfig" / "logs"


# ---------------------------------------------------------------------------
# default_log_dir — single source for the ~/.agent-runner/<name>/logs fallback,
# shared by log_dir's own missing-toml branch and api._resolve_target's
# bare-string branch (a project name, not a work_dir).


def test_default_log_dir_should_build_conventional_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert _resolve.default_log_dir("myproj") == tmp_path / ".agent-runner" / "myproj" / "logs"


def test_log_dir_should_match_default_log_dir_when_toml_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    work_dir = tmp_path / "noconfig"
    work_dir.mkdir()

    assert _resolve.log_dir(work_dir) == _resolve.default_log_dir("noconfig")


# ---------------------------------------------------------------------------
# guard_against_clobber -- api.install's same-basename sibling-unit guard


def test_guard_against_clobber_should_raise_when_existing_unit_owned_by_different_work_dir(
    tmp_path,
):
    serve_path = tmp_path / "agent-runner@proj.service"
    other_work_dir = tmp_path / "other-project"
    serve_path.write_text(f"WorkingDirectory={other_work_dir}\n")
    work_dir = tmp_path / "this-project"

    with pytest.raises(FileExistsError, match=str(other_work_dir)):
        _resolve.guard_against_clobber(serve_path, work_dir, force=False)


def test_guard_against_clobber_should_no_op_when_existing_unit_owned_by_same_work_dir(tmp_path):
    work_dir = tmp_path / "this-project"
    serve_path = tmp_path / "agent-runner@proj.service"
    serve_path.write_text(f"WorkingDirectory={work_dir}\n")

    _resolve.guard_against_clobber(serve_path, work_dir, force=False)


def test_guard_against_clobber_should_no_op_when_force_true_despite_different_owner(tmp_path):
    serve_path = tmp_path / "agent-runner@proj.service"
    other_work_dir = tmp_path / "other-project"
    serve_path.write_text(f"WorkingDirectory={other_work_dir}\n")
    work_dir = tmp_path / "this-project"

    _resolve.guard_against_clobber(serve_path, work_dir, force=True)


def test_guard_against_clobber_should_no_op_when_no_existing_unit_file(tmp_path):
    serve_path = tmp_path / "agent-runner@proj.service"
    work_dir = tmp_path / "this-project"

    _resolve.guard_against_clobber(serve_path, work_dir, force=False)
