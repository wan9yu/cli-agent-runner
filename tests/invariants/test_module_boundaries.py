"""Module boundary invariants — defends against:

- Ouroboros class (argus 5-rule #3): supervisor must not consume its own outputs
- Module sprawl: each subprocess/git/prompt concern lives in exactly one module
- §7 IMMUTABLE: runner is pure rotation, no event-driven branches
"""

from __future__ import annotations

import ast
from pathlib import Path

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
        "remote_relay.py",
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
    """Ouroboros defense (argus rule #3): runner writes events.jsonl but must never
    read it back. Strict since 0.2.11 — after the back-off moved to ``_throttle``,
    runner imports ``_throttle`` in NEITHER form, never globs ``events-*.jsonl``, and
    never opens an events file. The events-derived throttle state that drives back-off
    is read only by ``_throttle`` (which serve, not runner, calls)."""
    tree = ast.parse((PKG / "runner.py").read_text())
    import_targets: list[str] = []
    glob_patterns: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_targets += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            import_targets.append(mod)
            import_targets += [f"{mod}.{a.name}" for a in node.names]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("glob", "rglob")
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    glob_patterns.append(arg.value)
    assert import_targets, "no imports scanned in runner.py"  # vacuity-guard
    throttle_imports = [t for t in import_targets if "_throttle" in t]
    assert throttle_imports == [], f"runner.py imports _throttle (ouroboros): {throttle_imports}"
    events_globs = [g for g in glob_patterns if "events" in g]
    assert events_globs == [], f"runner.py globs events files (ouroboros read): {events_globs}"


def test_given_runner_module_when_scanned_then_only_imports_sibling_agent_runner_modules() -> None:
    runner = PKG / "runner.py"
    imports = _imports_in(runner)
    # fcntl is OK in runner (lock); subprocess is not OK.
    assert "subprocess" not in imports, "runner.py must not import subprocess directly"


def test_given_run_one_round_when_inspected_then_has_no_event_triggered_branches() -> None:
    """§7 IMMUTABLE — runner cannot branch on prior round state to choose work.

    No `if/elif` whose condition reads `last_exit_code` or `last_round_health` to
    switch code path. Phase rotation by round_num modulo len(phases) is fine (pure
    counter). Scans EVERY function in runner.py — the real body is
    `_run_one_round_inner`, which the old `run_one_round`-only scan skipped, so a
    branch hidden there would have passed. Reading `prev.last_exit_code` to *build the
    prompt context block* (a dict value, not an `if` test) is allowed — only `if`
    conditions are inspected."""
    tree = ast.parse((PKG / "runner.py").read_text())
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert any(f.name == "run_one_round" for f in funcs), (  # vacuity-guard
        "run_one_round not found in runner.py — scan target vanished"
    )
    offenders: list[tuple[str, int, str]] = []
    for fn in funcs:
        for sub in ast.walk(fn):
            if isinstance(sub, ast.If):
                src = ast.unparse(sub.test)
                if "last_exit_code" in src or "last_round_health" in src:
                    offenders.append((fn.name, sub.lineno, src))
    assert offenders == [], f"§7 violation — branch on prior round state: {offenders}"
