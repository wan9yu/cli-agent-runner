"""Boot-guard: a resume-capable preset's static command must not already
carry its resume_flag token (agent_runner/config/loader.py::_reject_static_resume_flag).

Also pins the shipped ``round_budget_s`` default against pi's own
``auto_retry`` back-off eating a round's timeout budget (see the static
budget test at the bottom of this file).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner import _plugin_manifest
from agent_runner._plugin_manifest import PluginManifest, register_manifest
from agent_runner.config import RuntimeConfig, load_config
from tests._test_helpers import isolating, write_min_config

_reset = isolating(_plugin_manifest._LOADED_MANIFESTS)


def test_load_config_should_reject_a_resume_preset_whose_command_already_has_the_flag_when_invoked(
    tmp_path,
):
    register_manifest(PluginManifest(name="pi", resume_flag="--session-id"))
    write_min_config(
        tmp_path, agent_extra='command = ["pi", "--session-id", "x", "--mode", "json"]\n'
    )

    with pytest.raises(ValueError, match="--session-id"):
        load_config(tmp_path / "agent-runner.toml")


def test_load_config_should_accept_a_resume_preset_without_the_flag_in_command_when_invoked(
    tmp_path,
):
    register_manifest(PluginManifest(name="pi", resume_flag="--session-id"))
    write_min_config(tmp_path, agent_extra='command = ["pi", "--mode", "json"]\n')

    load_config(tmp_path / "agent-runner.toml")


def test_default_round_budget_should_survive_a_60s_pi_auto_retry_when_invoked():
    """pi's own ``auto_retry`` backs off up to ~60s mid-round; the round-budget
    hang detector must not false-kill a retrying round, so the shipped default
    needs comfortable headroom above that ceiling."""
    assert RuntimeConfig(work_dir=Path("."), log_dir=Path(".")).round_budget_s >= 60
