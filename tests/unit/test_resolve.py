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


def test_project_name_lenient_accepts_spaced_basename(tmp_path):
    work_dir = tmp_path / "my project"
    assert _resolve.project_name(work_dir, strict=False) == "my project"


def test_project_name_lenient_accepts_cjk_basename(tmp_path):
    work_dir = tmp_path / "我的项目"
    assert _resolve.project_name(work_dir, strict=False) == "我的项目"


def test_project_name_strict_rejects_spaced_basename(tmp_path):
    work_dir = tmp_path / "my project"
    with pytest.raises(ValueError, match="invalid project name"):
        _resolve.project_name(work_dir, strict=True)


def test_project_name_strict_rejects_cjk_basename(tmp_path):
    work_dir = tmp_path / "我的项目"
    with pytest.raises(ValueError, match="invalid project name"):
        _resolve.project_name(work_dir, strict=True)


def test_project_name_strict_accepts_plain_basename(tmp_path):
    work_dir = tmp_path / "my-project_v1.2"
    assert _resolve.project_name(work_dir, strict=True) == "my-project_v1.2"


def test_project_name_lenient_also_accepts_plain_basename(tmp_path):
    work_dir = tmp_path / "my-project_v1.2"
    assert _resolve.project_name(work_dir, strict=False) == "my-project_v1.2"


# ---------------------------------------------------------------------------
# config_path — single source for "given CLI args, which toml"


def test_config_path_uses_explicit_config_arg(tmp_path):
    cfg_file = tmp_path / "agent-runner.toml"
    cfg_file.write_text("")
    args = SimpleNamespace(config=cfg_file)
    assert _resolve.config_path(args) == cfg_file.resolve()


def test_config_path_defaults_to_cwd_toml_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace()
    assert _resolve.config_path(args) == tmp_path.resolve() / "agent-runner.toml"


def test_config_path_returns_args_config_verbatim_when_work_dir_differs_from_toml_dir(
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


# ---------------------------------------------------------------------------
# unit_filename — thin wrap of the existing serve_unit_filename


def test_unit_filename_wraps_serve_unit_filename():
    assert _resolve.unit_filename("myproj") == serve_unit_filename("myproj")
    assert _resolve.unit_filename("myproj") == "agent-runner@myproj.service"


# ---------------------------------------------------------------------------
# log_dir — reads from config when present, conventional fallback otherwise


def test_log_dir_reads_from_config_when_present(tmp_path):
    (tmp_path / "p.md").write_text("hi")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\ncommand = ['echo']\nprompt_arg_template = ['{prompt}']\n"
        "[runtime]\nwork_dir = '.'\nlog_dir = 'custom-logs'\n[prompt]\nfile = 'p.md'\n"
    )
    assert _resolve.log_dir(tmp_path) == (tmp_path / "custom-logs").resolve()


def test_log_dir_falls_back_to_conventional_path_when_config_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    work_dir = tmp_path / "noconfig"
    work_dir.mkdir()
    assert _resolve.log_dir(work_dir) == tmp_path / ".agent-runner" / "noconfig" / "logs"
