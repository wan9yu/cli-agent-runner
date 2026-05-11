from __future__ import annotations

from pathlib import Path

import pytest

from agent_runner.prompt_loader import (
    assemble_prompt,
    load_prompt,
    strip_yaml_frontmatter,
)


def test_given_prompt_md_when_load_then_returns_text(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("Hello, agent.")
    assert load_prompt(p) == "Hello, agent."


def test_given_missing_prompt_when_load_then_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt(tmp_path / "nope.md")


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
    out = assemble_prompt(p, context={"round_num": 5, "phase": "diverge"}, inject_context=True)
    assert "round_num" in out
    assert "diverge" in out
    assert out.endswith("Do work.")


def test_given_prompt_when_assembled_without_inject_then_only_body(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("Body only.")
    out = assemble_prompt(p, context={"round_num": 1}, inject_context=False)
    assert out == "Body only."


def test_given_prompt_with_frontmatter_when_assembled_then_frontmatter_stripped(
    tmp_path: Path,
) -> None:
    p = tmp_path / "p.md"
    p.write_text("---\ntitle: x\n---\nBody.")
    out = assemble_prompt(p, context=None, inject_context=False)
    assert out == "Body."
