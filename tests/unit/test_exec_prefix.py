"""``AgentConfig.spawn_command`` — the SSOT splice of an opaque operator-supplied
``exec_prefix`` (e.g. ``docker run … img``) before ``command``, and both
consumers (runner spawn, startup battery) resolving through it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner import startup_check
from agent_runner.agent_runtime import _detect_container_run
from agent_runner.config.models import AgentConfig
from tests._test_helpers import FakeArgs, make_toml_with_sections, write_min_config


def test_spawn_command_should_splice_prefix_before_command_when_exec_prefix_set():
    agent = AgentConfig(
        command=["claude", "-p"],
        prompt_arg_template=[],
        exec_prefix=["docker", "run", "--rm", "-v", "{work_dir}:/work", "img"],
    )

    spawned = agent.spawn_command(Path("/srv/proj"))

    assert spawned == ["docker", "run", "--rm", "-v", "/srv/proj:/work", "img", "claude", "-p"]
    assert agent.binary == "claude"  # identity join unchanged by the prefix


def test_spawn_command_should_equal_command_when_no_exec_prefix():
    agent = AgentConfig(command=["claude", "-p"], prompt_arg_template=[])

    assert agent.spawn_command(Path("/srv/proj")) == ["claude", "-p"]
    assert agent.binary == "claude"


def test_agent_target_check_should_resolve_prefix_head_not_agent_when_containerized(tmp_path):
    agent = AgentConfig(
        command=["claude", "-p"], prompt_arg_template=[], exec_prefix=["docker", "run", "img"]
    )
    seen = []

    def fake_resolve(cli, work_dir, env_path=None):
        seen.append(cli)
        return "/usr/bin/docker"

    with patch("agent_runner.startup_check.agent_runtime.resolve_exec_target", fake_resolve):
        result = startup_check._check_agent_target(agent, tmp_path, "agent_cli_in_path")

    assert seen == ["docker"]  # resolves the prefix head on the host, not "claude"
    assert result.ok


def test_detect_container_run_should_fire_on_spawn_command_when_exec_prefix_is_docker():
    agent = AgentConfig(
        command=["claude", "-p"],
        prompt_arg_template=[],
        exec_prefix=["docker", "run", "--rm", "-v", "{work_dir}:/work", "img"],
    )

    assert _detect_container_run(agent.spawn_command(Path("/srv"))) is not None

    bare = AgentConfig(
        command=["claude", "-p"], prompt_arg_template=[], exec_prefix=["nice", "-n", "10"]
    )
    assert _detect_container_run(bare.spawn_command(Path("/srv"))) is None


def test_serve_should_disable_cgroup_defer_when_agent_exec_prefix_is_a_container_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = write_min_config(
        tmp_path, agent_extra='exec_prefix = ["docker", "run", "--rm", "img"]\n'
    )
    monkeypatch.setattr(serve_cmd, "_probe_and_emit_cgroup_defer", lambda log_dir: True)
    captured = {}

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **kwargs):
        round_log_path.write_text("round output\n")
        captured["defer_to_cgroup"] = kwargs["defer_to_cgroup"]
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=True))

    assert rc == 0
    assert captured["defer_to_cgroup"] is False


def test_serve_should_leave_cgroup_defer_governed_by_probe_when_exec_prefix_is_not_a_container_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = write_min_config(tmp_path, agent_extra='exec_prefix = ["nice", "-n", "10"]\n')
    monkeypatch.setattr(serve_cmd, "_probe_and_emit_cgroup_defer", lambda log_dir: True)
    captured = {}

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **kwargs):
        round_log_path.write_text("round output\n")
        captured["defer_to_cgroup"] = kwargs["defer_to_cgroup"]
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=True))

    assert rc == 0
    assert captured["defer_to_cgroup"] is True


def test_serve_should_compute_defer_to_cgroup_per_round_from_the_phase_actually_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """exec_prefix is base-only, but a phase CAN override command to a bare
    container run — so a stale, base-computed defer_to_cgroup would wrongly keep
    deferring a containerized phase's mid-round kill to dockerd's own cgroup-OOM.
    Same config, same probe result, two selections: the containerized phase
    forces the round-level defer off; the base agent (no --phase) leaves it
    governed by the probe."""
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml_with_sections(
        tmp_path,
        phases_block=(
            '[phases]\nlist = ["container"]\nphase_policy = "skip"\n'
            '[phases.container.agent]\ncommand = ["docker", "run", "--rm", "img"]\n'
        ),
    )
    monkeypatch.setattr(serve_cmd, "_probe_and_emit_cgroup_defer", lambda log_dir: True)
    captured = {}

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **kwargs):
        round_log_path.write_text("round output\n")
        captured["defer_to_cgroup"] = kwargs["defer_to_cgroup"]
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc_container = serve_cmd.cmd(FakeArgs(cfg_path, once=True))
    assert rc_container == 0
    assert captured["defer_to_cgroup"] is False  # containerized phase -> never defer

    rc_base = serve_cmd.cmd(FakeArgs(cfg_path, once=True, ignore_schedule=True))
    assert rc_base == 0
    assert captured["defer_to_cgroup"] is True  # base agent (no --phase) -> probe governs
