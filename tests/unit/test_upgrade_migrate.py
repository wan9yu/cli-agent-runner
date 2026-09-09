import argparse
from pathlib import Path

from agent_runner.cli import upgrade_cmd


def _cfg(tmp_path: Path, body: str) -> Path:
    (tmp_path / "logs").mkdir(exist_ok=True)
    p = tmp_path / "agent-runner.toml"
    p.write_text(body, encoding="utf-8")
    return p


# NOTE: {{prompt}} is escaped — this string is passed through str.format(wd=, ld=),
# so a bare {prompt} would raise KeyError.
_VALID = (
    '[agent]\ncommand = ["true"]\nprompt_arg_template = ["{{prompt}}"]\n'
    '[runtime]\nwork_dir = "{wd}"\nlog_dir = "{ld}"\n'
)


def test_migrate_config_file_should_apply_when_only_auto_renames_present(tmp_path):
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'rate_limit_action = "skip"\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)
    (tmp_path / "p.md").write_text("hi")

    applied, manual = upgrade_cmd._migrate_config_file(cfg, no_migrate=False)

    assert applied and not manual
    assert 'transient_error_action = "skip"' in cfg.read_text()
    assert (tmp_path / "agent-runner.toml.bak").exists()


def test_migrate_config_file_should_skip_when_no_migrate_flag_set(tmp_path):
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'rate_limit_action = "skip"\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)

    applied, manual = upgrade_cmd._migrate_config_file(cfg, no_migrate=True)

    assert applied == [] and manual == []
    assert 'rate_limit_action = "skip"' in cfg.read_text()  # untouched


def test_migrate_config_file_should_report_manual_when_only_manual_transform_present(tmp_path):
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'round_timeout_per_phase = { dev = 900 }\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)

    applied, manual = upgrade_cmd._migrate_config_file(cfg, no_migrate=False)

    assert applied == [] and len(manual) == 1


def test_migrate_config_file_should_leave_file_untouched_when_auto_and_manual_both_present(
    tmp_path,
):
    """Report-only path: never mutate a config on a doomed upgrade."""
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'rate_limit_action = "skip"\n'
        + 'round_timeout_per_phase = { dev = 900 }\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)
    before = cfg.read_bytes()

    applied, manual = upgrade_cmd._migrate_config_file(cfg, no_migrate=False)

    assert applied == [] and len(manual) == 1
    assert cfg.read_bytes() == before  # file untouched
    assert not (tmp_path / "agent-runner.toml.bak").exists()


def test_try_load_cfg_should_return_none_when_config_has_unmigratable_manual_key(tmp_path):
    """Defense-in-depth: must degrade to None, not raise ConfigError."""
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'round_timeout_per_phase = { dev = 900 }\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)
    args = argparse.Namespace(config=cfg)

    assert upgrade_cmd._try_load_cfg(args) is None


def test_try_load_cfg_should_return_none_when_toml_is_syntactically_broken(tmp_path):
    """Must degrade to None, not raise a raw TOMLDecodeError."""
    cfg = _cfg(tmp_path, "[runtime\n not toml")
    args = argparse.Namespace(config=cfg)

    assert upgrade_cmd._try_load_cfg(args) is None


def test_flat_phase_alias_should_not_be_treated_as_manual_remainder(tmp_path):
    """The flat [phases.<name>] alias is a permanent, still-valid form."""
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + '[prompt]\nfile = "p.md"\n'
        + '[phases]\nlist = ["a"]\n[phases.a]\nround_timeout_s = 900\n'
    )
    cfg = _cfg(tmp_path, body)
    (tmp_path / "p.md").write_text("hi")

    applied, manual = upgrade_cmd._migrate_config_file(cfg, no_migrate=False)

    assert manual == []


def test_cmd_should_mention_no_migrate_flag_when_manual_transform_required(tmp_path, capsys):
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'round_timeout_per_phase = { dev = 900 }\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)
    args = argparse.Namespace(config=str(cfg))

    rc = upgrade_cmd.cmd(args)

    assert rc == 1
    assert "--no-migrate" in capsys.readouterr().err
