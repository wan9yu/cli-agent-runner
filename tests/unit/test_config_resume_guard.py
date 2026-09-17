"""Boot-guard: a resume-capable preset's static command must not already
carry its resume_flag token (agent_runner/config/loader.py::_reject_static_resume_flag).
"""

from __future__ import annotations

import pytest

from agent_runner import _plugin_manifest
from agent_runner._plugin_manifest import PluginManifest, register_manifest
from agent_runner.config import load_config
from tests._test_helpers import isolating, write_min_config

_reset = isolating(_plugin_manifest._LOADED_MANIFESTS)


def test_load_config_should_reject_a_resume_preset_whose_command_already_has_the_flag(tmp_path):
    register_manifest(PluginManifest(name="pi", resume_flag="--session-id"))
    write_min_config(
        tmp_path, agent_extra='command = ["pi", "--session-id", "x", "--mode", "json"]\n'
    )

    with pytest.raises(ValueError, match="--session-id"):
        load_config(tmp_path / "agent-runner.toml")


def test_load_config_should_accept_a_resume_preset_without_the_flag_in_command(tmp_path):
    register_manifest(PluginManifest(name="pi", resume_flag="--session-id"))
    write_min_config(tmp_path, agent_extra='command = ["pi", "--mode", "json"]\n')

    load_config(tmp_path / "agent-runner.toml")
