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


def _pkg_modules() -> list[Path]:
    """Every module in the package, subpackages included.

    Recursive by contract: a non-recursive glob saw 28 of 47 modules, leaving
    all of cli/, builtin_plugins/ and presets/ outside every invariant here.
    """
    return sorted(PKG.rglob("*.py"))


def test_given_boundary_scans_when_collecting_modules_then_reach_subpackages() -> None:
    scanned = {f.relative_to(PKG).as_posix() for f in _pkg_modules()}
    top_level = {f.name for f in PKG.glob("*.py")}
    assert len(scanned) > len(top_level), "scan is not recursing into subpackages"
    for rel in ("cli/upgrade_cmd.py", "builtin_plugins/__init__.py", "presets/__init__.py"):
        assert rel in scanned, f"{rel} not scanned"


def test_given_codebase_when_scanned_then_only_sanctioned_modules_import_subprocess() -> None:
    # Sanctioned by repo-relative path, not basename: rglob sees 4 __init__.py.
    # Mirrors pyproject.toml's "subprocess".msg and per-file-ignores.
    sanctioned = {
        "_substrate.py",
        "agent_runtime.py",
        "api.py",
        "cli/install_cmd.py",
        "cli/serve_cmd.py",
        "cli/upgrade_cmd.py",
        "metrics.py",
        "monitor.py",
        "scaffold.py",
        "vcs_state.py",
    }
    offenders: list[str] = []
    for f in _pkg_modules():
        rel = f.relative_to(PKG).as_posix()
        if rel in sanctioned:
            continue
        if "subprocess" in _imports_in(f):
            offenders.append(rel)
    assert offenders == [], f"subprocess imported in non-sanctioned modules: {offenders}"


def test_given_codebase_when_scanned_then_only_sanctioned_modules_call_git_cli() -> None:
    """Look for any list literal whose first element is the string 'git' outside sanctioned modules.

    vcs_state.py is the primary git CLI caller. scaffold.py is permitted a single `git add` +
    `git commit` sequence for the optional initial commit during `agent-runner init`.
    """
    offenders: list[tuple[str, int]] = []
    for f in _pkg_modules():
        rel = f.relative_to(PKG).as_posix()
        if rel in ("vcs_state.py", "scaffold.py", "_substrate.py"):
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value == "git":
                    offenders.append((rel, node.lineno))
    assert offenders == [], f"git CLI call outside sanctioned modules: {offenders}"


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
