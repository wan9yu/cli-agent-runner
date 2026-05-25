"""Documentation generator — replaces <!-- gen:NAME --> ... <!-- /gen:NAME -->
content blocks in docs/*.md from registered renderers.

The marker primitive `replace_block` is intentionally separate from the
renderer registry so the substitution rule is testable in isolation.
"""

from __future__ import annotations

import dataclasses
import re
import typing
from collections.abc import Callable
from pathlib import Path

from agent_runner.config import (
    AgentConfig,
    Config,
    MonitorConfig,
    PromptConfig,
    RuntimeConfig,
    VcsConfig,
)
from agent_runner.defenses import catalog
from agent_runner.events import KNOWN_EVENT_KINDS
from agent_runner.monitor import AUTO_STOP_ALERTS, KNOWN_ALERT_KINDS

_SECTIONS = [
    ("agent", AgentConfig),
    ("runtime", RuntimeConfig),
    ("prompt", PromptConfig),
    ("vcs", VcsConfig),
    ("monitor", MonitorConfig),
]


def _type_label(t: typing.Any) -> str:
    # dataclasses.fields exposes `.type` as a string (PEP 563), so we render
    # the raw annotation. Strip ``Path`` / ``Path | None`` wrappers cosmetically.
    s = str(t).replace("pathlib.", "")
    return s


def _default_label(field: dataclasses.Field) -> str:
    if field.default is not dataclasses.MISSING:
        return repr(field.default)
    if field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return repr(field.default_factory())
    return "—"


def render_config_schema_table() -> str:
    """Markdown sub-sections per Config dataclass with field/type/default."""
    parts: list[str] = []
    for name, dc in _SECTIONS:
        parts.append(f"### `[{name}]`")
        parts.append("")
        parts.append("| Field | Type | Default |")
        parts.append("|---|---|---|")
        for f in dataclasses.fields(dc):
            parts.append(f"| `{f.name}` | `{_type_label(f.type)}` | {_default_label(f)} |")
        parts.append("")
    return "\n".join(parts).rstrip()


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


def render_detector_list() -> str:
    """Bullet list of detectors; auto-stop kinds flagged inline."""
    lines: list[str] = []
    for k in sorted(KNOWN_ALERT_KINDS):
        suffix = " — **auto-stop**" if k in AUTO_STOP_ALERTS else ""
        lines.append(f"- `{k}`{suffix}")
    return "\n".join(lines)


def render_event_kinds_list() -> str:
    """Flat bullet list of all known event kinds, alphabetised."""
    return "\n".join(f"- `{k}`" for k in sorted(KNOWN_EVENT_KINDS))


def render_verb_table() -> str:
    """Walk the argparse subparsers and render a verb table."""
    from agent_runner.cli import _build_parser

    parser = _build_parser()
    # Find the sub-parsers action — there's exactly one.
    sub_action = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    rows = [
        "| Verb | Description |",
        "|---|---|",
    ]
    for verb, _sp in sub_action.choices.items():
        # Argparse stores help text via `sub_action._choices_actions` indexed by add order.
        help_text = (
            next(
                (c.help for c in sub_action._choices_actions if c.dest == verb),
                "",
            )
            or ""
        )
        rows.append(f"| `{verb}` | {help_text} |")
    return "\n".join(rows)


RENDERERS: dict[str, Callable[[], str]] = {
    "defenses-table": render_defenses_table,
    "detector-list": render_detector_list,
    "event-kinds": render_event_kinds_list,
    "config-schema": render_config_schema_table,
    "verb-table": render_verb_table,
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
                    f"{md.name}: unknown gen marker {name!r} — valid names: {sorted(RENDERERS)}"
                )
            try:
                text = replace_block(text, name, RENDERERS[name]())
            except ValueError as e:
                raise ValueError(f"{md.name}: {e}") from e
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
