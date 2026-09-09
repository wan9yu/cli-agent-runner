from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.prompt_loader import (
    assemble_prompt,
    strip_yaml_frontmatter,
)


def test_text_with_yaml_frontmatter_should_have_frontmatter_removed_when_stripped() -> None:
    raw = "---\ntitle: foo\n---\n\nBody here."

    result = strip_yaml_frontmatter(raw)

    assert result == "Body here."


def test_text_without_frontmatter_should_be_unchanged_when_stripped() -> None:
    result = strip_yaml_frontmatter("No frontmatter.")

    assert result == "No frontmatter."


def test_text_with_only_opening_delim_should_be_unchanged_when_stripped() -> None:
    raw = "---\nno closing"

    result = strip_yaml_frontmatter(raw)

    assert result == raw


def test_prompt_should_have_context_block_prepended_when_assembled_with_context(
    tmp_path: Path,
) -> None:
    p = tmp_path / "p.md"
    p.write_text("Do work.")

    out = assemble_prompt([p], context={"round_num": 5, "phase": "diverge"}, inject_context=True)

    assert "round_num" in out
    assert "diverge" in out
    assert out.endswith("Do work.")


def test_prompt_should_return_only_body_when_assembled_without_inject(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("Body only.")

    out = assemble_prompt([p], context={"round_num": 1}, inject_context=False)

    assert out == "Body only."


def test_prompt_with_frontmatter_should_have_frontmatter_stripped_when_assembled(
    tmp_path: Path,
) -> None:
    p = tmp_path / "p.md"
    p.write_text("---\ntitle: x\n---\nBody.")

    out = assemble_prompt([p], context=None, inject_context=False)

    assert out == "Body."


# ---------------------------------------------------------------------------
# context_injection_mode tests
# ---------------------------------------------------------------------------


@pytest.fixture
def prompt_file(tmp_path: Path) -> Path:
    p = tmp_path / "main.md"
    p.write_text("# Agent Prompt\nDo the work.\n")
    return p


def test_prepend_mode_should_prepend_context_when_assembled(prompt_file: Path) -> None:
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=True,
        mode="prepend",
    )

    assert out.startswith("```json round-context\n")
    assert '"round_num": 1' in out
    assert "Do the work." in out


def test_file_mode_should_not_prepend_context_when_assembled(prompt_file: Path) -> None:
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=True,
        mode="file",
    )

    assert "round-context" not in out
    assert out.startswith("# Agent Prompt")


def test_none_mode_should_not_prepend_context_when_assembled(prompt_file: Path) -> None:
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=True,
        mode="none",
    )

    assert "round-context" not in out
    assert out.startswith("# Agent Prompt")


def test_inject_context_false_should_not_prepend_context_when_assembled(prompt_file: Path) -> None:
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=False,
        mode="prepend",
    )

    assert "round-context" not in out


def test_default_mode_should_prepend_context_when_mode_kwarg_omitted(prompt_file: Path) -> None:
    """mode defaults to 'prepend' for backward compat — call without mode kwarg."""
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=True,
    )

    assert out.startswith("```json round-context\n")
