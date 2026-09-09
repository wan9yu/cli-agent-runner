"""Tests for api.assemble_prompt (high-level, takes Config + phase)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import make_toml_with_sections as _toml


def test_assemble_prompt_should_concat_files_in_order_when_multiple_files_given(
    tmp_path: Path,
) -> None:
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("first")
    (tmp_path / "b.md").write_text("second")
    cfg = load_config(_toml(tmp_path, prompt_block='files = ["a.md", "b.md"]'))

    result = assemble_prompt(cfg, phase=None, context=None)

    assert result == "first\n\nsecond"


def test_assemble_prompt_should_use_custom_separator_when_configured(
    tmp_path: Path,
) -> None:
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

    result = assemble_prompt(cfg, phase=None, context=None)

    assert result == "first\n\n---\n\nsecond"


def test_assemble_prompt_should_raise_when_first_file_missing(tmp_path: Path) -> None:
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    cfg = load_config(_toml(tmp_path, prompt_block='files = ["nope.md"]'))

    with pytest.raises(FileNotFoundError, match=r"prompt\.files\[0\] missing"):
        assemble_prompt(cfg, phase=None, context=None)


def test_assemble_prompt_should_warn_and_skip_missing_file_when_not_first(
    tmp_path: Path, caplog
) -> None:
    import logging

    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("first")
    cfg = load_config(_toml(tmp_path, prompt_block='files = ["a.md", "missing.md"]'))

    with caplog.at_level(logging.WARNING):
        result = assemble_prompt(cfg, phase=None, context=None)

    assert result == "first"
    assert "missing.md" in caplog.text


def test_assemble_prompt_should_strip_frontmatter_from_first_file_by_default(
    tmp_path: Path,
) -> None:
    from agent_runner.api import assemble_prompt
    from agent_runner.config import load_config

    (tmp_path / "a.md").write_text("---\nyaml: stuff\n---\nbody preamble")
    (tmp_path / "b.md").write_text("body two")
    cfg = load_config(_toml(tmp_path, prompt_block='files = ["a.md", "b.md"]'))

    result = assemble_prompt(cfg, phase=None, context=None)

    assert result == "body preamble\n\nbody two"


def test_assemble_prompt_should_keep_frontmatter_when_strip_opted_out(
    tmp_path: Path,
) -> None:
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


def test_assemble_prompt_should_apply_per_phase_prompt_files_override(
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
