"""Prompt loader — knows prompt is a .md file, optionally injects round-context block.

R721 defense: strip YAML frontmatter before passing to claude CLI argv. A prompt
starting with `---\\n` is rejected by claude's arg parser as an unknown flag.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_prompt(prompt_file: Path) -> str:
    try:
        return prompt_file.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise FileNotFoundError(f"prompt file not found: {prompt_file}") from e


def strip_yaml_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    return text[end + len("\n---\n") :].lstrip()


def _format_context_block(context: dict[str, Any]) -> str:
    body = json.dumps(context, indent=2, ensure_ascii=False)
    return f"```json round-context\n{body}\n```\n\n"


def assemble_prompt(
    prompt_file: Path,
    *,
    context: dict[str, Any] | None,
    inject_context: bool,
) -> str:
    body = strip_yaml_frontmatter(load_prompt(prompt_file))
    if inject_context and context is not None:
        return _format_context_block(context) + body
    return body
