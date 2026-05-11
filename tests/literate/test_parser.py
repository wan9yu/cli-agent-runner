from __future__ import annotations

from tests.literate.parser import LiterateBlock, parse_literate_blocks  # noqa: F401


def test_given_md_with_single_bash_block_when_parsed_then_returns_one_block() -> None:
    md = "intro\n\n```bash\necho hi\n```\n"
    blocks = parse_literate_blocks(md)
    assert len(blocks) == 1
    assert blocks[0].code.strip() == "echo hi"
    assert blocks[0].expected_substring is None
    assert blocks[0].expected_status == 0
    assert blocks[0].skip is False
    assert blocks[0].env == {}


def test_given_md_with_assert_marker_when_parsed_then_records_substring() -> None:
    md = "```bash\necho hello\n```\n<!-- assert: hello -->\n"
    [block] = parse_literate_blocks(md)
    assert block.expected_substring == "hello"


def test_given_md_with_status_marker_when_parsed_then_records_status() -> None:
    md = "```bash\nfalse\n```\n<!-- assert-status: 1 -->\n"
    [block] = parse_literate_blocks(md)
    assert block.expected_status == 1


def test_given_md_with_skip_marker_when_parsed_then_marks_block_skipped() -> None:
    md = "```bash\nclaude /login\n```\n<!-- skip-test -->\n"
    [block] = parse_literate_blocks(md)
    assert block.skip is True


def test_given_md_with_env_marker_when_parsed_then_records_env() -> None:
    md = "```bash\necho $X\n```\n<!-- env: X=hi -->\n<!-- env: Y=bye -->\n"
    [block] = parse_literate_blocks(md)
    assert block.env == {"X": "hi", "Y": "bye"}


def test_given_md_with_non_bash_codeblock_when_parsed_then_skipped() -> None:
    md = "```python\nprint('x')\n```\n```bash\ntrue\n```\n"
    blocks = parse_literate_blocks(md)
    assert len(blocks) == 1
    assert blocks[0].code.strip() == "true"


def test_given_two_bash_blocks_when_parsed_then_keeps_order() -> None:
    md = "```bash\necho one\n```\n\n```bash\necho two\n```\n"
    blocks = parse_literate_blocks(md)
    assert [b.code.strip() for b in blocks] == ["echo one", "echo two"]
