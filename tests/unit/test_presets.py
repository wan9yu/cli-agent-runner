"""Invariants on shipped preset files."""

from __future__ import annotations

import importlib.resources
import tomllib

import pytest

PRESET_NAMES = ["claude", "aider"]


def _preset_text(name: str) -> str:
    return (importlib.resources.files("agent_runner.presets") / f"{name}.toml").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_given_preset_when_loaded_as_text_then_contains_project_placeholder(name: str) -> None:
    text = _preset_text(name)
    assert "{project}" in text, f"{name}.toml: missing {{project}} placeholder"


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_given_preset_when_substituted_and_parsed_then_valid_toml(name: str) -> None:
    text = _preset_text(name).replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    assert "agent" in parsed
    assert "runtime" in parsed
    assert "prompt" in parsed


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_given_preset_when_parsed_then_prompt_arg_template_contains_prompt(name: str) -> None:
    text = _preset_text(name).replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    template = parsed["agent"]["prompt_arg_template"]
    assert any("{prompt}" in arg for arg in template), (
        f"{name}.toml: prompt_arg_template lacks {{prompt}}"
    )


def test_given_claude_preset_when_parsed_then_includes_disable_autoupdater() -> None:
    text = _preset_text("claude").replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    env = parsed["agent"].get("env", {})
    assert env.get("DISABLE_AUTOUPDATER") == "1"
    assert env.get("CLAUDE_CODE_EFFORT_LEVEL") == "xhigh"


def test_given_aider_preset_when_parsed_then_no_agent_env_block() -> None:
    """Aider requires no env injection — preset omits [agent.env] entirely."""
    text = _preset_text("aider").replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    assert "env" not in parsed["agent"]


def test_given_aider_preset_when_parsed_then_uses_message_flag() -> None:
    """Aider one-shot mode uses --message; agent-runner substitutes {prompt}."""
    text = _preset_text("aider").replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    assert parsed["agent"]["prompt_arg_template"][0] == "--message"
    assert parsed["agent"]["command"][0] == "aider"
    assert "--yes-always" in parsed["agent"]["command"]
    assert "--analytics-disable" in parsed["agent"]["command"]


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_given_preset_when_parsed_then_monitor_auth_fail_hint_non_empty(name: str) -> None:
    text = _preset_text(name).replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    hint = parsed.get("monitor", {}).get("auth_fail_hint", "")
    assert hint, f"{name}.toml: [monitor].auth_fail_hint must be non-empty"


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_given_preset_when_full_load_via_load_config_then_no_errors(name: str, tmp_path) -> None:
    """End-to-end: preset → write file → load_config → valid Config object."""
    from agent_runner.config import load_config

    text = _preset_text(name).replace("{project}", "test-project")
    target = tmp_path / "agent-runner.toml"
    target.write_text(text)
    cfg = load_config(target)
    assert cfg.agent.command
    assert cfg.agent.prompt_arg_template


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_given_preset_when_loaded_then_no_deprecation_warnings(name: str, tmp_path) -> None:
    """Shipped presets must not emit DeprecationWarning on first load."""
    import warnings

    from agent_runner.config import load_config

    text = _preset_text(name).replace("{project}", "test-project")
    target = tmp_path / "agent-runner.toml"
    target.write_text(text)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_config(target)
    deps = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    msgs = [str(w.message) for w in deps]
    assert not deps, f"preset {name}.toml emitted {len(deps)} DeprecationWarning(s): {msgs}"
