"""``AgentConfig.spawn_command`` — the SSOT splice of an opaque operator-supplied
``exec_prefix`` (e.g. ``docker run … img``) before ``command``, and both
consumers (runner spawn, startup battery) resolving through it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent_runner import startup_check
from agent_runner.config.models import AgentConfig


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
