"""b18: a permanent startup-battery failure stops serve (config_broken) instead
of respawning a broken config forever."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.api import PERMANENT_CONFIG_EXIT
from tests._test_helpers import FakeArgs, make_toml, read_events_for_current_month


def test_given_round_exits_permanent_config_when_serve_then_config_broken_and_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s):
        round_log_path.write_text("")
        return PERMANENT_CONFIG_EXIT

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)
    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    # serve broke the loop (returned the give-up exit code) rather than respawning
    # the broken config, and recorded why.
    assert rc == PERMANENT_CONFIG_EXIT
    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "config_broken" in kinds


def test_given_invalid_max_rounds_when_serve_then_config_broken_and_78(
    tmp_path: Path,
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False, max_rounds=0))

    assert rc == PERMANENT_CONFIG_EXIT
    kinds = [e.get("event") for e in read_events_for_current_month(log_dir)]
    assert "config_broken" in kinds
