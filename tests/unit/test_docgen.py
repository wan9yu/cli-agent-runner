from __future__ import annotations

import pytest

from agent_runner._docgen import replace_block


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
