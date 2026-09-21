"""The SIGNAL half of the serve->child single-source (sibling to
test_apply_reap_grace_env): serve resolves the cooperative-stop signal ONCE per
phase from its own registry and publishes it via AGENT_RUNNER_COOPERATIVE_STOP_SIGNAL;
the round child reads that env and never re-derives the signal from the manifest
registry -- so signal and grace can't disagree under `[plugins] disable`."""

from __future__ import annotations

import dataclasses
import signal

from agent_runner import runner
from agent_runner._plugin_manifest import _LOADED_MANIFESTS, PluginManifest, register_manifest
from agent_runner.cli import serve_cmd
from agent_runner.config import AgentConfig, PhaseOverride, PhasesConfig
from tests._test_helpers import isolating, make_cfg

_reset = isolating(_LOADED_MANIFESTS)


def test_resolve_cooperative_signal_by_phase_should_resolve_phase_against_its_own_agent_when_run(
    tmp_path,
):
    register_manifest(PluginManifest(name="int_agent", cooperative_stop="SIGINT"))
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(cfg, agent=dataclasses.replace(cfg.agent, command=["claude"]))
    cfg = dataclasses.replace(
        cfg,
        phases=PhasesConfig(
            list=["int_phase"],
            overrides={
                "int_phase": PhaseOverride(
                    agent=AgentConfig(command=["int_agent"], prompt_arg_template=["{prompt}"])
                )
            },
        ),
    )

    signal_by_phase = serve_cmd._resolve_cooperative_signal_by_phase(cfg)

    # Base agent ("claude" binary) declares no matching manifest -> SIGTERM
    # default; the phase override resolves to its own SIGINT agent.
    assert signal_by_phase == {None: "SIGTERM", "int_phase": "SIGINT"}


def test_apply_cooperative_signal_env_should_publish_the_declared_signal_when_cooperative(tmp_path):
    register_manifest(PluginManifest(name="int_agent", cooperative_stop="SIGINT"))
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(cfg, agent=dataclasses.replace(cfg.agent, command=["int_agent"]))
    round_env: dict = {}
    signal_by_phase = serve_cmd._resolve_cooperative_signal_by_phase(cfg)

    serve_cmd._apply_cooperative_signal_env(cfg, round_env, None, signal_by_phase)

    assert round_env["AGENT_RUNNER_COOPERATIVE_STOP_SIGNAL"] == "SIGINT"


def test_apply_cooperative_signal_env_should_publish_sigterm_when_agent_is_not_cooperative(
    tmp_path,
):
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(cfg, agent=dataclasses.replace(cfg.agent, command=["kimi"]))
    round_env: dict = {}
    signal_by_phase = serve_cmd._resolve_cooperative_signal_by_phase(cfg)

    serve_cmd._apply_cooperative_signal_env(cfg, round_env, None, signal_by_phase)

    # None cooperative_stop == SIGTERM-first at the reap site (today's behavior),
    # never "skip the first signal".
    assert round_env["AGENT_RUNNER_COOPERATIVE_STOP_SIGNAL"] == "SIGTERM"


def test_apply_cooperative_signal_env_should_fall_back_when_phase_not_precomputed(tmp_path):
    register_manifest(PluginManifest(name="int_agent", cooperative_stop="SIGINT"))
    cfg = make_cfg(tmp_path)
    cfg = dataclasses.replace(cfg, agent=dataclasses.replace(cfg.agent, command=["int_agent"]))
    round_env: dict = {}

    serve_cmd._apply_cooperative_signal_env(cfg, round_env, "unprecomputed_phase", {})

    assert round_env["AGENT_RUNNER_COOPERATIVE_STOP_SIGNAL"] == "SIGINT"


def test_runner_should_read_the_published_signal_from_env_when_invoked(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_COOPERATIVE_STOP_SIGNAL", "SIGINT")

    assert runner._resolve_cooperative_signal() is signal.SIGINT


def test_runner_should_fall_back_to_sigterm_when_env_absent(monkeypatch):
    monkeypatch.delenv("AGENT_RUNNER_COOPERATIVE_STOP_SIGNAL", raising=False)

    assert runner._resolve_cooperative_signal() is signal.SIGTERM


def test_runner_should_fall_back_to_sigterm_when_env_is_an_unknown_name(monkeypatch):
    # A leaked / hand-set standalone env value must never reach getattr(signal, ...)
    # and land on SIGKILL/SIGSTOP -- unknown names fall back to SIGTERM.
    monkeypatch.setenv("AGENT_RUNNER_COOPERATIVE_STOP_SIGNAL", "SIGKILL")

    assert runner._resolve_cooperative_signal() is signal.SIGTERM
