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


def test_preflight_migrate_applies_auto_before_load(tmp_path):
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


def test_no_migrate_skips(tmp_path):
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'rate_limit_action = "skip"\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)
    applied, manual = upgrade_cmd._migrate_config_file(cfg, no_migrate=True)
    assert applied == [] and manual == []
    assert 'rate_limit_action = "skip"' in cfg.read_text()  # untouched


def test_manual_transform_reported(tmp_path):
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'round_timeout_per_phase = { dev = 900 }\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)
    applied, manual = upgrade_cmd._migrate_config_file(cfg, no_migrate=False)
    assert applied == [] and len(manual) == 1


def test_mixed_auto_and_manual_writes_nothing(tmp_path):
    """A config with BOTH an auto rename and a manual transform is report-only:
    the upgrade aborts, so _migrate_config_file must leave the file byte-for-byte
    unchanged and write no `.bak` (never mutate a config on a doomed upgrade)."""
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


def test_try_load_cfg_returns_none_on_unmigratable(tmp_path):
    """Defense-in-depth: a config that still carries a removed key after any
    auto-migration must degrade to the package-only path (None), not crash
    with a ConfigError traceback."""
    body = (
        _VALID.format(wd=tmp_path, ld=tmp_path / "logs")
        + 'round_timeout_per_phase = { dev = 900 }\n[prompt]\nfile = "p.md"\n'
    )
    cfg = _cfg(tmp_path, body)
    args = argparse.Namespace(config=cfg)
    assert upgrade_cmd._try_load_cfg(args) is None
