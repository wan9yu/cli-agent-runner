"""Tests for monitor --mode http CLI dispatch + flag handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import make_toml


def test_cli_main_should_dispatch_to_cmd_http_when_mode_is_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import main, monitor_cmd

    cfg_path = make_toml(tmp_path)

    captured = {}

    def fake_cmd_http(args) -> int:
        captured["port"] = args.port
        return 0

    monkeypatch.setattr(monitor_cmd, "_cmd_http", fake_cmd_http)

    rc = main(["--config", str(cfg_path), "monitor", "--mode", "http", "--port", "8765"])

    assert rc == 0
    assert captured["port"] == 8765


def test_cli_main_should_error_when_mode_http_with_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`monitor --mode http --host pi` rejected (local-only, like narrate/events)."""
    from agent_runner.cli import main

    cfg_path = make_toml(tmp_path)

    rc = main(["--config", str(cfg_path), "monitor", "--mode", "http", "--host", "pi"])

    assert rc != 0
