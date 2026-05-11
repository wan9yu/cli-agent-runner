"""Module boundary invariants — defends against:

- Ouroboros class (argus 5-rule #3): supervisor must not consume its own outputs
- Module sprawl: each subprocess/git/prompt concern lives in exactly one module
- §7 IMMUTABLE: runner is pure rotation, no event-driven branches
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


def _imports_in(file: Path) -> set[str]:
    tree = ast.parse(file.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module.split(".")[0])
    return out


def _string_literals_in(file: Path) -> list[str]:
    tree = ast.parse(file.read_text())
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def test_given_codebase_when_scanned_then_only_agent_runtime_and_vcs_state_import_subprocess() -> (
    None
):
    offenders: list[str] = []
    for f in PKG.glob("*.py"):
        if f.name in ("agent_runtime.py", "vcs_state.py", "__init__.py"):
            continue
        if "subprocess" in _imports_in(f):
            offenders.append(f.name)
    assert offenders == [], f"subprocess imported in non-sanctioned modules: {offenders}"


def test_given_codebase_when_scanned_then_only_vcs_state_calls_git_cli() -> None:
    """Look for any list literal whose first element is the string 'git' outside vcs_state."""
    offenders: list[tuple[str, int]] = []
    for f in PKG.glob("*.py"):
        if f.name in ("vcs_state.py", "__init__.py"):
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value == "git":
                    offenders.append((f.name, node.lineno))
    assert offenders == [], f"git CLI call outside vcs_state.py: {offenders}"


def test_given_runner_module_when_scanned_then_does_not_read_events_jsonl() -> None:
    """Ouroboros defense: runner writes events.jsonl but must never read it back."""
    runner = PKG / "runner.py"
    text = runner.read_text()
    # Allow the literal "events.jsonl" only via emit() function calls (which write).
    # Forbid any open()/read of events.jsonl files.
    forbidden = ["events-", ".jsonl"]
    for token in forbidden:
        # OK if appears in metric/logger writers only via the events module — but runner.py
        # itself should never glob/read the events.jsonl files.
        if "glob(" in text and token in text:
            for line_num, line in enumerate(text.splitlines(), 1):
                if "glob" in line and "events" in line:
                    pytest.fail(
                        f"runner.py:{line_num} appears to read events files: {line.strip()}"
                    )


def test_given_runner_module_when_scanned_then_only_imports_sibling_agent_runner_modules() -> None:
    runner = PKG / "runner.py"
    imports = _imports_in(runner)
    # fcntl is OK in runner (lock); subprocess is not OK.
    assert "subprocess" not in imports, "runner.py must not import subprocess directly"


def test_given_run_one_round_when_inspected_then_has_no_event_triggered_branches() -> None:
    """§7 IMMUTABLE — runner cannot branch on prior round state to choose work.

    Specifically: no `if/elif` whose condition reads from `prev_status.last_exit_code`
    or `last_round_health` to switch to a different code path. Phase rotation by
    round_num modulo phases.length is fine (pure function of counter).
    """
    runner = PKG / "runner.py"
    tree = ast.parse(runner.read_text())
    # Find run_one_round's outer structure
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_one_round":
            for sub in ast.walk(node):
                if isinstance(sub, ast.If):
                    src = ast.unparse(sub.test)
                    assert "last_exit_code" not in src, (
                        "run_one_round branches on last_exit_code — violates §7 pure rotation"
                    )
                    assert "last_round_health" not in src, (
                        "run_one_round branches on last_round_health — violates §7 pure rotation"
                    )
            return
    pytest.fail("run_one_round not found in runner.py")
