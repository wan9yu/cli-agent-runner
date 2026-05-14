"""Prompt loader — knows prompt is a .md file, optionally injects round-context block.

R721 defense: strip YAML frontmatter before passing to claude CLI argv. A prompt
starting with `---\\n` is rejected by claude's arg parser as an unknown flag.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger(__name__)


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
    prompt_files: list[Path],
    *,
    context: dict[str, Any] | None,
    inject_context: bool,
    mode: Literal["prepend", "file", "none"] = "prepend",
    concat_separator: str = "\n\n",
    strip_first_frontmatter: bool = True,
) -> str:
    """Assemble prompt: concat files, optionally strip first-file YAML frontmatter, inject context.

    ``prompt_files`` must be non-empty; ``prompt_files[0]`` must exist. Missing
    files at index >= 1 are logged as warnings and skipped (supports the
    optional-preamble pattern).
    """
    if not prompt_files:
        raise ValueError("assemble_prompt: prompt_files must be non-empty")
    first = prompt_files[0]
    if not first.exists():
        raise FileNotFoundError(f"prompt.files[0] missing: {first}")
    bodies: list[str] = []
    for i, path in enumerate(prompt_files):
        if not path.exists():
            _log.warning("prompt.files[%d] missing: %s — skipping", i, path)
            continue
        body = path.read_text(encoding="utf-8")
        if i == 0 and strip_first_frontmatter:
            body = strip_yaml_frontmatter(body)
        bodies.append(body)
    body = concat_separator.join(bodies)
    if inject_context and context is not None and mode == "prepend":
        return _format_context_block(context) + body
    return body
