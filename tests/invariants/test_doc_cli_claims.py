"""Invariants: doc CLI examples and doc code blocks must match the real code.

Every claim guarded here shipped, and each produces a hard failure for an
operator or plugin author who follows it. The scans are deliberately narrow —
they pin the SSOT (argparse, the entry-point loader, the imported module)
rather than pattern-matching prose.
"""

from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Docs that hand an operator a copy-pasteable command line.
_CLI_DOCS = ("docs/commands.md", "docs/runbook.md", "docs/quickstart.md")

# `agent-runner <verb> --flag ...` inside fenced blocks or prose. A leading
# `--` is matched by [a-z-]+ too; those verbs simply miss the choices lookup.
_CLI_LINE_RE = re.compile(r"agent-runner\s+([a-z-]+)((?:\s+--?[\w-]+)+)")


def _subparser_choices() -> dict[str, object]:
    from agent_runner.cli import _build_parser

    parser = _build_parser()
    subs = [a for a in parser._subparsers._group_actions if hasattr(a, "choices")]
    return {c: p for a in subs for c, p in a.choices.items()}


def _real_entry_point_groups() -> set[str]:
    """Every entry-point group agent_runner actually loads, read from the loader."""
    import agent_runner

    src = Path(agent_runner.__file__).read_text(encoding="utf-8")
    return set(re.findall(r'"(agent_runner\.[a-z_]+)"', src))


def test_given_doc_cli_examples_when_parsed_then_every_flag_exists() -> None:
    """Every --flag a doc hands an operator must exist on that verb's parser."""
    choices = _subparser_choices()

    failures: list[str] = []
    for fname in _CLI_DOCS:
        text = (REPO / fname).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for verb, flags in _CLI_LINE_RE.findall(line):
                if verb not in choices:
                    continue
                known = {opt for act in choices[verb]._actions for opt in act.option_strings}
                for flag in re.findall(r"--?[\w-]+", flags):
                    if flag not in known:
                        failures.append(f"{fname}:{lineno}: `{verb}` has no flag {flag}")
    assert not failures, "doc CLI flag drift:\n" + "\n".join(failures)


def test_given_docs_when_scanned_then_no_notimplementederror_claim() -> None:
    """No doc may claim a code path raises NotImplementedError: none does."""
    src = "\n".join(p.read_text(encoding="utf-8") for p in (REPO / "agent_runner").rglob("*.py"))
    assert "NotImplementedError" not in src, (
        "source now raises NotImplementedError — this invariant's premise changed"
    )
    # Non-recursive: docs/internal is gitignored, docs/migrations is frozen history.
    failures: list[str] = []
    for path in sorted((REPO / "docs").glob("*.md")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "NotImplementedError" in line:
                failures.append(f"docs/{path.name}:{lineno}: {line.strip()}")
    assert not failures, "docs claim NotImplementedError but no source raises it:\n" + "\n".join(
        failures
    )


def test_given_doc_python_blocks_when_scanned_then_imported_symbols_exist() -> None:
    """`from agent_runner.X import Y` in docs/plugins.md must resolve."""
    text = (REPO / "docs/plugins.md").read_text(encoding="utf-8")
    failures: list[str] = []
    for mod_path, names in re.findall(
        r"^from (agent_runner[\w.]*) import ([^\n(]+)$", text, re.MULTILINE
    ):
        try:
            mod = importlib.import_module(mod_path)
        except ImportError as e:
            # Reported, not raised: a module deleted without its doc section is
            # the same class of drift this test exists to name.
            failures.append(f"docs/plugins.md: cannot import {mod_path} ({e})")
            continue
        for name in (n.strip() for n in names.split(",")):
            if name and not hasattr(mod, name):
                failures.append(f"docs/plugins.md: {mod_path} has no attribute {name!r}")
    assert not failures, "doc imports do not resolve:\n" + "\n".join(failures)


def test_given_doc_entry_point_groups_when_scanned_then_loader_loads_them() -> None:
    """An entry-point group a doc tells a plugin author to register under must be
    one the loader actually scans — otherwise the plugin silently never loads."""
    real = _real_entry_point_groups()
    text = (REPO / "docs/plugins.md").read_text(encoding="utf-8")
    documented = set(re.findall(r'\[project\.entry-points\."(agent_runner\.[\w.]+)"\]', text))
    unknown = documented - real
    assert not unknown, (
        f"docs/plugins.md documents entry-point groups the loader never scans: "
        f"{sorted(unknown)}; real groups: {sorted(real)}"
    )


def test_given_documented_handle_dirty_when_compared_then_signature_matches() -> None:
    """docs/plugins.md's DirtyHandler recipe must match the real Protocol."""
    from agent_runner.hooks import DirtyHandler

    real = [p for p in inspect.signature(DirtyHandler.handle_dirty).parameters if p != "self"]
    text = (REPO / "docs/plugins.md").read_text(encoding="utf-8")
    block = re.search(r"def handle_dirty\(\s*(.*?)\s*\) ->", text, re.DOTALL)
    assert block, "docs/plugins.md no longer shows a handle_dirty signature"
    documented = [m for m in re.findall(r"^\s*(\w+)", block.group(1), re.MULTILINE) if m != "self"]
    assert documented == real, (
        f"docs/plugins.md documents handle_dirty{tuple(documented)}; "
        f"hooks.DirtyHandler declares {tuple(real)}"
    )
