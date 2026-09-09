"""b18: a permanent, non-self-healing round failure (ConfigError-classified —
a startup-battery check or any other permanent verdict) stops serve
(config_broken) instead of respawning a broken config forever."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.api import PERMANENT_CONFIG_EXIT
from agent_runner.cli import serve_cmd
from agent_runner.config import ConfigError
from tests._test_helpers import FakeArgs, make_toml, read_events_for_current_month


def test_serve_should_report_config_broken_and_stop_when_round_permanently_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        round_log_path.write_text("")
        return PERMANENT_CONFIG_EXIT

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    # serve broke the loop (returned the give-up exit code) rather than respawning
    # the broken config, and recorded why.
    assert rc == PERMANENT_CONFIG_EXIT
    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "config_broken" in kinds


def test_serve_should_report_config_broken_and_stop_when_max_rounds_invalid(
    tmp_path: Path,
) -> None:
    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=0))

    assert rc == PERMANENT_CONFIG_EXIT
    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "config_broken" in kinds


def test_serve_should_raise_config_error_when_config_missing_at_boot(
    tmp_path: Path,
) -> None:
    """Group A: a bad initial load (never even started the loop) is PERMANENT --
    a single transient bad load is fatal to serve, not a 5-restart crash loop.
    cmd() raises ConfigError uncaught (main() maps it to 78); this pins the
    exception type main()'s handler relies on, mirroring round_cmd's own
    boundary conversion for the identical FileNotFoundError."""
    with pytest.raises(ConfigError):
        serve_cmd.cmd(FakeArgs(tmp_path / "nope.toml", once=False))


def test_serve_should_raise_config_error_when_toml_syntax_broken_at_boot(
    tmp_path: Path,
) -> None:
    bad_toml = tmp_path / "agent-runner.toml"
    bad_toml.write_text("this is not [valid toml")

    with pytest.raises(ConfigError):
        serve_cmd.cmd(FakeArgs(bad_toml, once=False))
