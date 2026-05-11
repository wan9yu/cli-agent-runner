"""Parse a markdown file into LiterateBlock objects for the literate runner.

Markdown DSL on top of standard markdown:

* ``` ```bash ``` block — code to execute.
* ``<!-- assert: SUBSTR -->`` on the line(s) after the closing ``` — stdout must contain SUBSTR.
* ``<!-- assert-status: N -->`` — block must exit with code N (default 0).
* ``<!-- skip-test -->`` — skip executing the next-prior bash block.
* ``<!-- env: KEY=VAL -->`` — inject env vars (may repeat).

Markers must be contiguous — a blank line after a block's markers ends the
marker run, so visual separators between blocks don't accidentally bind
later markers to the earlier block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class LiterateBlock:
    code: str
    expected_substring: str | None = None
    expected_status: int = 0
    skip: bool = False
    env: dict[str, str] = field(default_factory=dict)
    line: int = 0  # markdown line number of the opening ```bash fence


_BASH_RE = re.compile(r"^```bash\s*$", re.MULTILINE)
_FENCE_CLOSE = re.compile(r"^```\s*$", re.MULTILINE)
_ASSERT_RE = re.compile(r"<!--\s*assert:\s*(.+?)\s*-->")
_STATUS_RE = re.compile(r"<!--\s*assert-status:\s*(-?\d+)\s*-->")
_SKIP_RE = re.compile(r"<!--\s*skip-test\s*-->")
_ENV_RE = re.compile(r"<!--\s*env:\s*([A-Z_][A-Z0-9_]*)=(.*?)\s*-->")


def parse_literate_blocks(md_text: str) -> list[LiterateBlock]:
    blocks: list[LiterateBlock] = []
    lines = md_text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if _BASH_RE.match(line.rstrip("\n")):
            open_idx = i
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not _FENCE_CLOSE.match(lines[i].rstrip("\n")):
                code_lines.append(lines[i])
                i += 1
            # i now points at the closing ```; advance past it
            i += 1
            block = LiterateBlock(code="".join(code_lines), line=open_idx + 1)
            # Look at marker comments on subsequent lines until a blank or non-comment line.
            while i < len(lines):
                ln = lines[i].strip()
                if not ln:
                    break  # blank ends the marker run for this block
                if not ln.startswith("<!--"):
                    break
                if m := _ASSERT_RE.search(ln):
                    block.expected_substring = m.group(1)
                elif m := _STATUS_RE.search(ln):
                    block.expected_status = int(m.group(1))
                elif _SKIP_RE.search(ln):
                    block.skip = True
                elif m := _ENV_RE.search(ln):
                    block.env[m.group(1)] = m.group(2)
                # Unknown comments are ignored (treated as plain markdown comments).
                i += 1
            blocks.append(block)
        else:
            i += 1
    return blocks
