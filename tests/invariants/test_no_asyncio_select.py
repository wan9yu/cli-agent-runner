"""Remainder: asyncio subprocess .kill/.terminate after ratch ConfinedImport.

Ratch 0.1.6 ``ConfinedImport`` keeps ``asyncio`` / ``select`` / ``selectors`` /
``os.pidfd_open`` out of ``agent_runner/**`` except ``_procwait.py`` and
``_notify.py``. This file keeps the CPython#127049 shape: ``.kill()`` /
``.terminate()`` on a process assigned from ``asyncio.create_subprocess_*``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._test_helpers import ROOT

_PKG = ROOT / "agent_runner"

_ASYNCIO_SPAWN_ATTRS = {"create_subprocess_exec", "create_subprocess_shell"}


def _asyncio_process_kill_hits(path: Path) -> list[str]:
    """Flags ``.kill()``/``.terminate()`` called on a variable assigned from
    ``asyncio.create_subprocess_exec``/``create_subprocess_shell`` (``await``ed
    or not). Deliberately does NOT flag a bare ``proc.terminate()`` in
    general -- that is this codebase's own ``subprocess.Popen`` reap idiom.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    async_proc_vars: set[str] = set()

    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue

        if isinstance(value, ast.Await):
            value = value.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr in _ASYNCIO_SPAWN_ATTRS
            and isinstance(target, ast.Name)
        ):
            async_proc_vars.add(target.id)

    hits: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("kill", "terminate")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in async_proc_vars
        ):
            hits.append(f"L{node.lineno} {node.func.value.id}.{node.func.attr}()")
    return hits


def test_asyncio_kill_else_terminate_should_never_appear_in_agent_runner_when_invoked() -> None:
    offenders: dict[str, list[str]] = {}
    scanned = 0

    for path in _PKG.rglob("*.py"):
        rel = str(path.relative_to(_PKG))
        scanned += 1
        hits = _asyncio_process_kill_hits(path)
        if hits:
            offenders[rel] = hits

    assert scanned > 0, "no agent_runner/*.py modules scanned"  # vacuity-guard
    assert not offenders, (
        f"asyncio subprocess .kill()/.terminate() found: {offenders}. This is the exact "
        f"CPython#127049 PID-reuse hazard that single-owner reap "
        f"(agent_runtime._kill_pgroup, cli._serve_round._terminate_round) exists to avoid."
    )


def test_scanner_should_flag_asyncio_process_kill_when_scanned(tmp_path: Path) -> None:
    kill_offender = tmp_path / "kill_offender.py"
    kill_offender.write_text(
        "import asyncio\n"
        "async def f():\n"
        "    proc = await asyncio.create_subprocess_exec('sleep', '1')\n"
        "    proc.kill()\n"
    )
    assert _asyncio_process_kill_hits(kill_offender), (
        "scanner failed to flag asyncio Process.kill()"
    )

    clean = tmp_path / "clean.py"
    clean.write_text("import subprocess\ndef f(proc: subprocess.Popen):\n    proc.terminate()\n")
    assert not _asyncio_process_kill_hits(clean)
