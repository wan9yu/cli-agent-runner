from __future__ import annotations

import pytest

from agent_runner._docgen import render_defenses_table, replace_block


def test_given_text_with_block_when_replaced_then_returns_new_content_between_markers() -> None:
    text = (
        "intro line\n"
        "<!-- gen:foo -->\n"
        "OLD CONTENT\n"
        "<!-- /gen:foo -->\n"
        "trailing line\n"
    )
    got = replace_block(text, "foo", "NEW CONTENT")
    assert "OLD CONTENT" not in got
    assert "NEW CONTENT" in got
    assert "<!-- gen:foo -->" in got
    assert "<!-- /gen:foo -->" in got
    assert got.startswith("intro line")
    assert got.endswith("trailing line\n")


def test_given_text_without_block_when_replaced_then_returns_unchanged() -> None:
    text = "some markdown without markers\n"
    assert replace_block(text, "missing", "X") == text


def test_given_unclosed_block_when_replaced_then_raises_valueerror() -> None:
    text = "<!-- gen:foo -->\nstuff\n(never closes)\n"
    with pytest.raises(ValueError, match="foo"):
        replace_block(text, "foo", "X")


def test_given_default_cfg_when_render_defenses_table_then_returns_markdown_table() -> None:
    md = render_defenses_table()
    # Header
    assert "| Defense | Codifies | Guarded by |" in md
    assert "|---|---|---|" in md
    # Eleven entries
    rows = [
        line
        for line in md.splitlines()
        if line.startswith("| ") and "Defense" not in line and "---" not in line[:5]
    ]
    assert len(rows) == 11
    # Spot-check one well-known defense
    assert "round_timeout_s" in md


def test_given_render_defenses_table_when_called_then_paths_render_relative() -> None:
    md = render_defenses_table()
    # No absolute paths should leak — guarded_by is rendered as repo-relative.
    assert "/Users/" not in md
    assert "tests/unit/test_agent_runtime.py" in md  # one known guarded_by
