"""Documentation generator — replaces <!-- gen:NAME --> ... <!-- /gen:NAME -->
content blocks in docs/*.md from registered renderers.

The marker primitive `replace_block` is intentionally separate from the
renderer registry so the substitution rule is testable in isolation.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.defenses import catalog


def replace_block(text: str, name: str, new_content: str) -> str:
    """Replace the body between ``<!-- gen:NAME -->`` and ``<!-- /gen:NAME -->``.

    The opening / closing markers themselves are preserved. Returns the
    original text unchanged when the opening marker is absent. Raises
    ``ValueError`` when the opening marker is present without a matching close.
    """
    open_tag = f"<!-- gen:{name} -->"
    close_tag = f"<!-- /gen:{name} -->"
    if open_tag not in text:
        return text
    if close_tag not in text:
        raise ValueError(f"<!-- gen:{name} --> has no matching close tag")
    pattern = re.compile(
        re.escape(open_tag) + r".*?" + re.escape(close_tag),
        re.DOTALL,
    )
    return pattern.sub(f"{open_tag}\n{new_content}\n{close_tag}", text)


def _default_cfg() -> Config:
    """Build a default Config for doc rendering — defaults only, no user values."""
    return Config(
        agent=AgentConfig(command=["agent"], prompt_arg_template=[]),
        runtime=RuntimeConfig(
            work_dir=Path("."),
            log_dir=Path("./logs"),
        ),
        prompt=PromptConfig(file=Path("./prompt.md")),
        vcs=VcsConfig(),
    )


def render_defenses_table() -> str:
    """Markdown table of the defense catalog. Renders defaults only."""
    cfg = _default_cfg()
    lines = [
        "| Defense | Codifies | Guarded by |",
        "|---|---|---|",
    ]
    for d in catalog(cfg):
        codifies = d.codifies or "—"
        guarded = str(d.guarded_by) if d.guarded_by is not None else "—"
        lines.append(f"| `{d.name}` | {codifies} | `{guarded}` |")
    return "\n".join(lines)
