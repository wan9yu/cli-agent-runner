"""Tests for cli.serve_cmd._apply_reap_grace_env -- the supervisor-side half
of the v0.3.5 cooperative-grace resolution (agent_runner/_plugin_manifest.py
::resolve_sigterm_grace_s is the pure decision; this wires it into round_env,
the per-round supervisor->round-child publish channel AGENT_RUNNER_ROUND_NUM
also uses)."""

from __future__ import annotations

import dataclasses

from agent_runner._plugin_manifest import _LOADED_MANIFESTS, PluginManifest, register_manifest
from agent_runner.cli import serve_cmd
from tests._test_helpers import isolating, make_cfg

_reset = isolating(_LOADED_MANIFESTS)


def test_apply_reap_grace_env_should_publish_configured_grace_when_agent_is_cooperative(tmp_path):
    register_manifest(PluginManifest(name="fake_coop", sigterm_cooperative=True))
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg, agent=dataclasses.replace(cfg.agent, command=["fake_coop"], sigterm_grace_s=9)
    )
    round_env: dict = {}

    serve_cmd._apply_reap_grace_env(cfg, round_env, None)

    assert round_env["AGENT_RUNNER_REAP_GRACE_S"] == "9"


def test_apply_reap_grace_env_should_publish_default_when_agent_is_not_cooperative(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg, agent=dataclasses.replace(cfg.agent, command=["claude"], sigterm_grace_s=9)
    )
    round_env: dict = {}

    serve_cmd._apply_reap_grace_env(cfg, round_env, None)

    assert round_env["AGENT_RUNNER_REAP_GRACE_S"] == "5"
