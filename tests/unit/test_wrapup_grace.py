"""``[runtime] wrapup_grace_s`` only ever widens the round-terminate grace
window, and only for an agent whose registered PluginManifest declares
sigterm_cooperative=True -- serve_cmd.py's call site is the ONE place both
facts (which binary is resolved, whether the operator opted in) combine into
``extra_grace_s``. Mirrors test_exec_prefix.py's defer_to_cgroup call-site
tests (same fake_spawn-capture technique)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import _plugin_manifest
from agent_runner.cli import serve_cmd
from tests._test_helpers import FakeArgs, make_toml_with_sections


def test_serve_should_ignore_wrapup_grace_s_when_agent_is_not_cooperative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_path = make_toml_with_sections(tmp_path, runtime_extra="wrapup_grace_s = 30\n")
    captured = {}

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **kwargs):
        round_log_path.write_text("round output\n")
        captured["extra_grace_s"] = kwargs["extra_grace_s"]
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=True))

    assert rc == 0
    assert captured["extra_grace_s"] == 0


def test_serve_should_ignore_cooperative_agent_when_wrapup_grace_s_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _plugin_manifest.register_manifest(
        _plugin_manifest.PluginManifest(name="true", sigterm_cooperative=True)
    )
    cfg_path = make_toml_with_sections(tmp_path)
    captured = {}

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **kwargs):
        round_log_path.write_text("round output\n")
        captured["extra_grace_s"] = kwargs["extra_grace_s"]
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=True))

    assert rc == 0
    assert captured["extra_grace_s"] == 0


def test_serve_should_thread_wrapup_grace_s_into_extra_grace_s_when_agent_is_cooperative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _plugin_manifest.register_manifest(
        _plugin_manifest.PluginManifest(name="true", sigterm_cooperative=True)
    )
    cfg_path = make_toml_with_sections(tmp_path, runtime_extra="wrapup_grace_s = 30\n")
    captured = {}

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **kwargs):
        round_log_path.write_text("round output\n")
        captured["extra_grace_s"] = kwargs["extra_grace_s"]
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=True))

    assert rc == 0
    assert captured["extra_grace_s"] == 30
