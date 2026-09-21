"""Invariant: the fd-wait fast path (``select``/``selectors``/``os.pidfd_open``)
stays confined to ``_notify.py`` + ``_procwait.py``, and ``asyncio`` never
enters ``agent_runner/`` at all.

Two footprint/safety rationales, both explained at length in
``_procwait.py``'s module docstring:

- Confinement: every other module must go through ``_procwait.wait_exit`` (or
  ``_notify``'s FIFO doorbell) rather than opening its own ``select``/
  ``selectors``/pidfd registration -- one reviewed fd-wait implementation, not
  N ad hoc copies drifting apart.
- No asyncio: this codebase's single-owner reap discipline
  (``agent_runtime._kill_pgroup``, ``cli._serve_round._terminate_round``) is
  immune to CPython's pidfd/child-watcher race
  (https://github.com/python/cpython/issues/127049) specifically BECAUSE
  there is no background reaper -- asyncio's subprocess transport runs one.
  Reintroducing ``asyncio`` (even unused elsewhere) reopens that door; a
  ``.kill()``/``.terminate()`` call on an asyncio subprocess ``Process`` is
  the exact shape of the hazard, asserted separately (distinct message) even
  though the plain "no asyncio import" ban above already forbids it.

The scan RESOLVES import bindings first (aliased ``import select as s``, or
``os`` under any alias for the ``.pidfd_open`` attribute check) -- the same
technique ``test_no_raw_time.py`` uses, so an aliased import can't dodge it.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._test_helpers import ROOT

_PKG = ROOT / "agent_runner"

# The only two modules allowed to touch select/selectors/os.pidfd_open -- the
# fd-driven fast path this whole scan protects.
_CONFINED_TO = {"_notify.py", "_procwait.py"}

# select.<attr> forms caught via module-binding resolution -- select.select
# (the blocking call itself) plus the kqueue registration surface
# (select.kqueue/kevent; select.KQ_* constants matched by prefix below).
_SELECT_MODULE_ATTRS = {"select", "kqueue", "kevent"}

# asyncio spawn-method names that hand back a Process with .kill()/.terminate()
# -- names unique to asyncio's subprocess API, so matching on the attribute
# name alone (no need to resolve the receiver back to an `asyncio` binding)
# cannot collide with subprocess.Popen or anything else in this codebase.
_ASYNCIO_SPAWN_ATTRS = {"create_subprocess_exec", "create_subprocess_shell"}


def _mod_bindings(tree: ast.AST) -> dict[str, tuple[str, str]]:
    """Map each imported name to ``('mod', 'select'|'os')`` for plain module
    imports (covers ``as`` aliases) -- used to resolve ``os.pidfd_open`` /
    ``select.kqueue`` even through a renamed binding."""
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("select", "os"):
                    out[a.asname or a.name] = ("mod", a.name)
    return out


def _confinement_hits(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    binds = _mod_bindings(tree)
    hits: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("select", "selectors"):
                    suffix = f" as {a.asname}" if a.asname else ""
                    hits.append(f"L{node.lineno} import {a.name}{suffix}")
        elif isinstance(node, ast.ImportFrom):
            if node.module in ("select", "selectors"):
                names = ", ".join(a.name for a in node.names)
                hits.append(f"L{node.lineno} from {node.module} import {names}")
            elif node.module == "os":
                for a in node.names:
                    if a.name == "pidfd_open":
                        hits.append(f"L{node.lineno} from os import pidfd_open")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            b = binds.get(node.value.id)
            if b is None:
                continue
            _kind, origin = b
            if origin == "os" and node.attr == "pidfd_open":
                hits.append(f"L{node.lineno} {node.value.id}.pidfd_open")
            elif origin == "select" and (
                node.attr in _SELECT_MODULE_ATTRS or node.attr.startswith("KQ_")
            ):
                hits.append(f"L{node.lineno} {node.value.id}.{node.attr}")

    return hits


def _asyncio_import_hits(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "asyncio" or a.name.startswith("asyncio."):
                    suffix = f" as {a.asname}" if a.asname else ""
                    hits.append(f"L{node.lineno} import {a.name}{suffix}")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "asyncio" or (node.module or "").startswith("asyncio."):
                names = ", ".join(a.name for a in node.names)
                hits.append(f"L{node.lineno} from {node.module} import {names}")
    return hits


def _asyncio_process_kill_hits(path: Path) -> list[str]:
    """Flags ``.kill()``/``.terminate()`` called on a variable assigned from
    ``asyncio.create_subprocess_exec``/``create_subprocess_shell`` (``await``ed
    or not). Deliberately does NOT flag a bare ``proc.terminate()`` in
    general -- that is this codebase's own ``subprocess.Popen`` reap idiom
    (``_terminate_round``, ``agent_runtime._kill_pgroup``), which must stay
    unflagged; only the asyncio-spawned shape is the hazard."""
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


def test_select_and_pidfd_should_stay_confined_to_the_fast_path_modules_when_invoked() -> None:
    offenders: dict[str, list[str]] = {}
    scanned = 0

    for path in _PKG.rglob("*.py"):
        rel = str(path.relative_to(_PKG))
        if rel in _CONFINED_TO:
            continue
        scanned += 1
        hits = _confinement_hits(path)
        if hits:
            offenders[rel] = hits

    assert scanned > 0, "no agent_runner/*.py modules scanned"  # vacuity-guard
    assert not offenders, (
        f"select/selectors/os.pidfd_open used outside the confined fast-path modules "
        f"({sorted(_CONFINED_TO)}): {offenders}. Route any new fd-wait need through "
        f"_procwait.wait_exit instead of opening a fresh select/pidfd registration."
    )


def test_asyncio_should_never_be_imported_in_agent_runner_when_invoked() -> None:
    offenders: dict[str, list[str]] = {}
    scanned = 0

    for path in _PKG.rglob("*.py"):
        rel = str(path.relative_to(_PKG))
        scanned += 1
        hits = _asyncio_import_hits(path)
        if hits:
            offenders[rel] = hits

    assert scanned > 0, "no agent_runner/*.py modules scanned"  # vacuity-guard
    assert not offenders, (
        f"import asyncio found in agent_runner/: {offenders}. agent_runner has zero asyncio "
        f"dependency today (footprint) -- see _procwait.py's module docstring for why the "
        f"fd-driven wait_exit design doesn't need it."
    )


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
        f"CPython#127049 PID-reuse hazard (a background asyncio child-watcher can reap a "
        f"zombie's pid slot between 'decide to signal' and 'actually signal', letting a "
        f"freshly spawned unrelated process get signalled by mistake) that this codebase's "
        f"single-owner reap discipline (agent_runtime._kill_pgroup, "
        f"cli._serve_round._terminate_round) exists to avoid."
    )


def test_scanner_should_flag_reintroduced_select_else_asyncio_when_scanned(tmp_path: Path) -> None:
    """Non-vacuity proof: plant the three offending shapes and confirm each
    scanner actually catches them, plus a clean file passes all three."""
    select_offender = tmp_path / "select_offender.py"
    select_offender.write_text(
        "import select as sel\nimport os as _os\n"
        "def f(fd):\n    sel.select([fd], [], [], 1)\n    _os.pidfd_open(1, 0)\n"
    )
    assert _confinement_hits(select_offender), "scanner failed to flag a reintroduced select use"

    asyncio_offender = tmp_path / "asyncio_offender.py"
    asyncio_offender.write_text("import asyncio as aio\n")
    assert _asyncio_import_hits(asyncio_offender), "scanner failed to flag a reintroduced import"

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
    clean.write_text(
        "import subprocess\n"
        "import os\n"
        "def f(proc: subprocess.Popen):\n"
        "    proc.terminate()\n"  # legitimate Popen usage -- must NOT be flagged
        "    os.getpid()\n"
    )
    assert not _confinement_hits(clean)
    assert not _asyncio_import_hits(clean)
    assert not _asyncio_process_kill_hits(clean)
