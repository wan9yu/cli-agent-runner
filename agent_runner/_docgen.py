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


RENDERERS: dict[str, object] = {
    "defenses-table": render_defenses_table,
}

_GEN_OPEN = re.compile(r"<!-- gen:([a-z0-9-]+) -->")


def render(docs_dir: Path, *, write: bool = True) -> dict[Path, str]:
    """Render every ``<!-- gen:NAME -->`` block in ``docs_dir/*.md``.

    Returns a {path: rendered_text} mapping. When ``write=True`` also writes
    the rewritten text back to each path.

    Raises ``ValueError`` when a marker references an unknown renderer name.
    """
    out: dict[Path, str] = {}
    for md in sorted(docs_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        for match in _GEN_OPEN.finditer(text):
            name = match.group(1)
            if name not in RENDERERS:
                raise ValueError(
                    f"{md.name}: unknown gen marker {name!r} — "
                    f"valid names: {sorted(RENDERERS)}"
                )
            text = replace_block(text, name, RENDERERS[name]())
        out[md] = text
        if write:
            md.write_text(text, encoding="utf-8")
    return out


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    render(docs_dir=repo_root / "docs")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
