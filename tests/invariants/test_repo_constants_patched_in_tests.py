"""#407 真凶 defense: tests that invoke run_one_round / stash_orphan etc. MUST
either patch the relevant repo path constants to tmp_path OR the call site must
be a no-op fixture/mock.

Argus 2026-04: a pytest fixture forgot to patch ``status_store.REPO_DIR`` →
real production git repo got 15 phantom ORPHAN stashes over a week. Same
class of bug here: any test invoking ``runner.run_one_round`` must point its
config at a tmp_path-based work_dir.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
DANGEROUS_CALLS = {"run_one_round", "stash_orphan", "_run_one_round_inner"}


def test_given_test_files_when_scanned_then_dangerous_calls_use_tmp_path_or_repo() -> None:
    offenders: list[tuple[str, int, str]] = []
    for f in TESTS.rglob("test_*.py"):
        text = f.read_text()
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                # Find dangerous calls inside this test
                calls = []
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        name = None
                        if isinstance(sub.func, ast.Name):
                            name = sub.func.id
                        elif isinstance(sub.func, ast.Attribute):
                            name = sub.func.attr
                        if name in DANGEROUS_CALLS:
                            calls.append(sub)
                if not calls:
                    continue
                # The test signature must accept tmp_path / tmp_git_repo / tmp_log_dir
                params = {a.arg for a in node.args.args}
                safe_fixtures = {"tmp_path", "tmp_git_repo", "tmp_log_dir", "fake_agent_script"}
                if not (params & safe_fixtures):
                    offenders.append((f.name, node.lineno, node.name))
    assert offenders == [], (
        f"tests calling {DANGEROUS_CALLS} without tmp_path-style fixture: {offenders}"
    )
