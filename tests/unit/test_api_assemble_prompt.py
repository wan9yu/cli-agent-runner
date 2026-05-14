"""Tests for api.assemble_prompt (high-level, takes Config + phase)."""

from __future__ import annotations

from pathlib import Path

import pytest


def _toml(tmp_path: Path, *, prompt_block: str, phases_block: str = "") -> Path:
    """Build a minimal TOML at tmp_path with custom [prompt] body."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        "[prompt]\n" + prompt_block + "\n" + phases_block
    )
    return toml


def test_given_files_list_when_assemble_then_concat_in_order(tmp_path: Path) -> None:
    """prompt.files = [a, b] → concat with default '\\n\\n' separator."""
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("first")
    (tmp_path / "b.md").write_text("second")
    cfg = load_config(_toml(tmp_path, prompt_block='files = ["a.md", "b.md"]'))
    assert assemble_prompt(cfg, phase=None, context=None) == "first\n\nsecond"


def test_given_custom_separator_when_assemble_then_honored(tmp_path: Path) -> None:
    """Custom concat_separator used between files."""
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("first")
    (tmp_path / "b.md").write_text("second")
    cfg = load_config(
        _toml(
            tmp_path,
            prompt_block='files = ["a.md", "b.md"]\nconcat_separator = "\\n\\n---\\n\\n"',
        )
    )
    assert assemble_prompt(cfg, phase=None, context=None) == "first\n\n---\n\nsecond"


def test_given_missing_files_0_when_assemble_then_raises(tmp_path: Path) -> None:
    """Missing first file → raises with clear message."""
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    cfg = load_config(_toml(tmp_path, prompt_block='files = ["nope.md"]'))
    with pytest.raises(FileNotFoundError, match=r"prompt\.files\[0\] missing"):
        assemble_prompt(cfg, phase=None, context=None)


def test_given_missing_nth_file_when_assemble_then_warns_and_skips(tmp_path: Path, caplog) -> None:
    """Missing 2nd file → log warning, skip, continue."""
    import logging

    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("first")
    cfg = load_config(_toml(tmp_path, prompt_block='files = ["a.md", "missing.md"]'))
    with caplog.at_level(logging.WARNING):
        result = assemble_prompt(cfg, phase=None, context=None)
    assert result == "first"
    assert "missing.md" in caplog.text


def test_given_strip_frontmatter_default_when_assemble_then_first_file_stripped(
    tmp_path: Path,
) -> None:
    """Default strip_yaml_frontmatter=True → first file's leading frontmatter removed."""
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("---\nyaml: stuff\n---\nbody preamble")
    (tmp_path / "b.md").write_text("body two")
    cfg = load_config(_toml(tmp_path, prompt_block='files = ["a.md", "b.md"]'))
    result = assemble_prompt(cfg, phase=None, context=None)
    assert result == "body preamble\n\nbody two"


def test_given_strip_frontmatter_opt_out_when_assemble_then_frontmatter_kept(
    tmp_path: Path,
) -> None:
    """strip_yaml_frontmatter=false → frontmatter preserved."""
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("---\nyaml: stuff\n---\nbody")
    cfg = load_config(
        _toml(
            tmp_path,
            prompt_block='files = ["a.md"]\nstrip_yaml_frontmatter = false',
        )
    )
    result = assemble_prompt(cfg, phase=None, context=None)
    assert result == "---\nyaml: stuff\n---\nbody"


def test_given_per_phase_prompt_files_when_assemble_then_override_applied(
    tmp_path: Path,
) -> None:
    """[phases.qa] prompt.files = [...] fully replaces global prompt.files."""
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "common.md").write_text("preamble")
    (tmp_path / "dev.md").write_text("dev body")
    (tmp_path / "qa.md").write_text("qa body")
    cfg = load_config(
        _toml(
            tmp_path,
            prompt_block='files = ["common.md", "dev.md"]',
            phases_block=(
                '[phases]\nlist = ["dev", "qa"]\n'
                '[phases.qa]\nprompt.files = ["common.md", "qa.md"]\n'
            ),
        )
    )
    assert assemble_prompt(cfg, phase="qa", context=None) == "preamble\n\nqa body"
    assert assemble_prompt(cfg, phase="dev", context=None) == "preamble\n\ndev body"
