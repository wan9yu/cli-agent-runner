from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.prompt_loader import (
    assemble_prompt,
    strip_yaml_frontmatter,
)


def test_given_text_with_yaml_frontmatter_when_stripped_then_frontmatter_removed() -> None:
    raw = "---\ntitle: foo\n---\n\nBody here."
    assert strip_yaml_frontmatter(raw) == "Body here."


def test_given_text_without_frontmatter_when_stripped_then_unchanged() -> None:
    assert strip_yaml_frontmatter("No frontmatter.") == "No frontmatter."


def test_given_text_with_only_opening_delim_when_stripped_then_unchanged() -> None:
    raw = "---\nno closing"
    assert strip_yaml_frontmatter(raw) == raw


def test_given_prompt_when_assembled_with_context_then_context_block_prepended(
    tmp_path: Path,
) -> None:
    p = tmp_path / "p.md"
    p.write_text("Do work.")
    out = assemble_prompt([p], context={"round_num": 5, "phase": "diverge"}, inject_context=True)
    assert "round_num" in out
    assert "diverge" in out
    assert out.endswith("Do work.")


def test_given_prompt_when_assembled_without_inject_then_only_body(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("Body only.")
    out = assemble_prompt([p], context={"round_num": 1}, inject_context=False)
    assert out == "Body only."


def test_given_prompt_with_frontmatter_when_assembled_then_frontmatter_stripped(
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


def test_given_prepend_mode_when_assembled_then_context_prepended(prompt_file: Path) -> None:
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=True,
        mode="prepend",
    )
    assert out.startswith("```json round-context\n")
    assert '"round_num": 1' in out
    assert "Do the work." in out


def test_given_file_mode_when_assembled_then_no_prepend(prompt_file: Path) -> None:
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=True,
        mode="file",
    )
    assert "round-context" not in out
    assert out.startswith("# Agent Prompt")


def test_given_none_mode_when_assembled_then_no_prepend(prompt_file: Path) -> None:
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=True,
        mode="none",
    )
    assert "round-context" not in out
    assert out.startswith("# Agent Prompt")


def test_given_inject_context_false_when_assembled_then_no_prepend(prompt_file: Path) -> None:
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=False,
        mode="prepend",
    )
    assert "round-context" not in out


def test_given_default_mode_when_assembled_then_prepend(prompt_file: Path) -> None:
    """mode defaults to 'prepend' for backward compat — call without mode kwarg."""
    out = assemble_prompt(
        [prompt_file],
        context={"round_num": 1},
        inject_context=True,
    )
    assert out.startswith("```json round-context\n")
