"""Tests for cli.serve_cmd._apply_reap_grace_env -- the supervisor-side half
of the v0.3.5 cooperative-grace resolution (agent_runner/_plugin_manifest.py
::resolve_sigterm_grace_s is the pure decision; this wires it into round_env,
the per-round supervisor->round-child publish channel AGENT_RUNNER_ROUND_NUM
also uses)."""

from __future__ import annotations

import dataclasses

from agent_runner._plugin_manifest import _LOADED_MANIFESTS, PluginManifest, register_manifest
from agent_runner.cli import serve_cmd
from agent_runner.config import AgentConfig, PhaseOverride, PhasesConfig
from tests._test_helpers import isolating, make_cfg

_reset = isolating(_LOADED_MANIFESTS)


def test_resolve_reap_grace_by_phase_should_resolve_each_phase_against_its_own_agent(tmp_path):
    """A phase override's own agent (cooperative or not) resolves
    independently of the base [agent] table and every other phase --
    the precomputed dict isn't just the base grace copied per key."""
    register_manifest(PluginManifest(name="fake_coop", cooperative_stop="SIGTERM"))
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg, agent=dataclasses.replace(cfg.agent, command=["claude"], sigterm_grace_s=9)
    )
    cfg = dataclasses.replace(
        cfg,
        phases=PhasesConfig(
            list=["coop_phase"],
            overrides={
                "coop_phase": PhaseOverride(
                    agent=AgentConfig(
                        command=["fake_coop"],
                        prompt_arg_template=["{prompt}"],
                        sigterm_grace_s=12,
                    )
                )
            },
        ),
    )

    grace_by_phase = serve_cmd._resolve_reap_grace_by_phase(cfg)

    assert grace_by_phase == {None: 5, "coop_phase": 12}


def test_apply_reap_grace_env_should_publish_configured_grace_when_agent_is_cooperative(tmp_path):
    register_manifest(PluginManifest(name="fake_coop", cooperative_stop="SIGTERM"))
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg, agent=dataclasses.replace(cfg.agent, command=["fake_coop"], sigterm_grace_s=9)
    )
    round_env: dict = {}
    grace_by_phase = serve_cmd._resolve_reap_grace_by_phase(cfg)

    serve_cmd._apply_reap_grace_env(cfg, round_env, None, grace_by_phase)

    assert round_env["AGENT_RUNNER_REAP_GRACE_S"] == "9"


def test_apply_reap_grace_env_should_publish_default_when_agent_is_not_cooperative(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg, agent=dataclasses.replace(cfg.agent, command=["claude"], sigterm_grace_s=9)
    )
    round_env: dict = {}
    grace_by_phase = serve_cmd._resolve_reap_grace_by_phase(cfg)

    serve_cmd._apply_reap_grace_env(cfg, round_env, None, grace_by_phase)

    assert round_env["AGENT_RUNNER_REAP_GRACE_S"] == "5"


def test_apply_reap_grace_env_should_fall_back_to_live_resolution_when_phase_not_precomputed(
    tmp_path,
):
    """Defensive path: a phase_arg absent from the precomputed dict (should
    never happen -- every phase_arg serve can select is one of
    _resolve_reap_grace_by_phase's keys) still resolves correctly instead of
    KeyError-crashing the round."""
    register_manifest(PluginManifest(name="fake_coop", cooperative_stop="SIGTERM"))
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(
        cfg, agent=dataclasses.replace(cfg.agent, command=["fake_coop"], sigterm_grace_s=9)
    )
    round_env: dict = {}

    serve_cmd._apply_reap_grace_env(cfg, round_env, "unprecomputed_phase", {})

    assert round_env["AGENT_RUNNER_REAP_GRACE_S"] == "9"
