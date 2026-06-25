"""b18: a permanent startup-battery failure stops serve (config_broken) instead
of respawning a broken config forever."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_runner.api import PERMANENT_CONFIG_EXIT
from tests._test_helpers import FakeArgs, make_toml


def _event_kinds(log_dir: Path) -> list[str]:
    kinds: list[str] = []
    for f in log_dir.glob("events-*.jsonl"):
        for line in f.read_text().splitlines():
            if line.strip():
                kinds.append(json.loads(line).get("event"))
    return kinds


def test_given_round_exits_permanent_config_when_serve_then_config_broken_and_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"

    def fake_run(*_a, **_k):
        return type("R", (), {"returncode": PERMANENT_CONFIG_EXIT, "stdout": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    # serve broke the loop (returned) rather than respawning the broken config,
    # and recorded why.
    assert rc == 0
    assert "config_broken" in _event_kinds(log_dir)
