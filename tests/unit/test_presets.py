"""Invariants on shipped preset files."""

from __future__ import annotations

import importlib.resources
import tomllib

import pytest

PRESET_NAMES = ["claude", "aider", "gemini", "codewhale", "kimi"]


def test_given_preset_names_when_compared_to_shipped_dir_then_match() -> None:
    """Guard the test-side PRESET_NAMES against the shipped presets/*.toml.

    `init_cmd._preset_names()` derives the CLI choices from the filesystem; this
    keeps the parametrize list (needed as a collection-time literal) from
    silently drifting when a preset .toml is added without updating tests.
    """
    presets = importlib.resources.files("agent_runner.presets")
    shipped = sorted(p.name[:-5] for p in presets.iterdir() if p.name.endswith(".toml"))
    assert sorted(PRESET_NAMES) == shipped, (
        f"PRESET_NAMES drifted from agent_runner/presets/*.toml: "
        f"hardcoded={sorted(PRESET_NAMES)} shipped={shipped}"
    )


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
    """argv-delivery presets must place {prompt} in the template; stdin-delivery
    presets (e.g. claude) deliberately omit it — the prompt travels on stdin
    instead, so it never lands in process argv."""
    text = _preset_text(name).replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    template = parsed["agent"]["prompt_arg_template"]
    if parsed["agent"].get("prompt_delivery", "argv") == "stdin":
        assert not any("{prompt}" in arg for arg in template), (
            f"{name}.toml: stdin delivery but prompt_arg_template still has {{prompt}}"
        )
    else:
        assert any("{prompt}" in arg for arg in template), (
            f"{name}.toml: prompt_arg_template lacks {{prompt}}"
        )


def test_given_claude_preset_when_parsed_then_uses_stdin_delivery() -> None:
    """claude preset opts into stdin prompt delivery: -p with no {prompt} arg."""
    text = _preset_text("claude").replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    assert parsed["agent"]["prompt_arg_template"] == ["-p"]
    assert parsed["agent"]["prompt_delivery"] == "stdin"


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


def test_given_gemini_preset_when_parsed_then_includes_skip_trust() -> None:
    """gemini --skip-trust required for unattended operation in untrusted dirs.

    Same semantic as claude's --dangerously-skip-permissions.
    """
    text = _preset_text("gemini").replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    assert "--skip-trust" in parsed["agent"]["command"], (
        "gemini preset must include --skip-trust; gemini CLI refuses headless "
        "operation in untrusted directories."
    )


def test_given_gemini_preset_when_parsed_then_uses_stream_json_output_format() -> None:
    """gemini -o stream-json required so gemini_error_detector plugin can parse JSONL.

    Pre-0.1.26 the preset shipped -o text (human-readable), which the plugin
    cannot parse — meaning agent_usage_recorded events never fired for gemini
    rounds in production. Fixed in 0.1.26 by switching to -o stream-json.
    """
    text = _preset_text("gemini").replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    cmd = parsed["agent"]["command"]
    assert "-o" in cmd, f"gemini preset must include -o flag, got: {cmd}"
    o_idx = cmd.index("-o")
    assert cmd[o_idx + 1] == "stream-json", (
        f"gemini preset must use -o stream-json (plugin requires JSONL); "
        f"got {cmd[o_idx + 1]!r}. See docs/migrations/0.1.26.md."
    )


def test_given_claude_preset_when_parsed_then_excludes_shell_snapshot() -> None:
    text = _preset_text("claude").replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    patterns = parsed["runtime"].get("grace_kill_ignore_patterns", [])
    assert any("shell-snapshots/snapshot-bash-" in p for p in patterns), (
        f"claude.toml: expected grace_kill_ignore_patterns to include the "
        f"shell-snapshot pattern, got {patterns}"
    )


@pytest.mark.parametrize("name", ["aider", "gemini", "codewhale"])
def test_given_other_presets_when_parsed_then_no_default_ignore_patterns(name: str) -> None:
    text = _preset_text(name).replace("{project}", "test-project")
    parsed = tomllib.loads(text)
    patterns = parsed.get("runtime", {}).get("grace_kill_ignore_patterns", [])
    assert patterns == [], (
        f"{name}.toml: should not ship grace_kill_ignore_patterns, got {patterns}"
    )


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


def test_given_codewhale_preset_when_parsed_then_uses_exec_stream_json() -> None:
    text = _preset_text("codewhale").replace("{project}", "test-project")
    cmd = tomllib.loads(text)["agent"]["command"]
    assert cmd[0] == "codewhale" and cmd[1] == "exec"
    assert "--auto" in cmd
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "stream-json"


def test_given_codewhale_preset_when_parsed_then_no_agent_env_block() -> None:
    text = _preset_text("codewhale").replace("{project}", "test-project")
    assert "env" not in tomllib.loads(text)["agent"]
