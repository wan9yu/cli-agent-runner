"""Invariant: reading the current time / sleeping happens ONLY in clock.py.

Every other module must take a ``Clock`` (or read ``clock.SYSTEM_CLOCK``) so time
is injectable — one ``FakeClock`` pins epoch/monotonic/sleep/now for a whole test
instead of a monkeypatch per call site. See ``agent_runner/clock.py``.

The scan RESOLVES import bindings first, so aliased (``import time as _t``) and
from-imported (``from time import sleep``) forms cannot dodge it — a real review
caught two such leaks that the naive ``time.<attr>`` match missed. ``_ALLOWLIST``
is empty: the migration is complete, and a new current-time read anywhere but
``clock.py`` fails here. Adding a file back is a regression — migrate it instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"

# Attributes that read the current time / block, per source. Conversions
# (fromisoformat/fromtimestamp) and formatting a KNOWN datetime (dt.strftime) are
# pure and absent — only *reading now* or *sleeping* is forbidden outside clock.py.
_TIME_MOD_ATTRS = {
    "time",
    "monotonic",
    "sleep",
    "strftime",
    "localtime",
    "gmtime",
    "perf_counter",
    "time_ns",
    "monotonic_ns",
    "process_time",
}
_DATETIME_CLS_ATTRS = {"now", "utcnow", "today"}
_DATE_CLS_ATTRS = {"today"}

_ALLOWLIST: set[str] = set()  # migration complete — keep empty


def _bindings(tree: ast.AST) -> dict[str, tuple[str, str]]:
    """Map each imported name to ``(kind, origin)``: ('mod','time'|'datetime'),
    ('cls','datetime'|'date'), or ('func', <time attr>) for ``from time import x``.
    Covers ``as`` aliases and function-local imports (walked, so conservative)."""
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("time", "datetime"):
                    out[a.asname or a.name] = ("mod", a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "time":
                for a in node.names:
                    out[a.asname or a.name] = ("func", a.name)  # bare-name time call
            elif node.module == "datetime":
                for a in node.names:
                    if a.name in ("datetime", "date"):
                        out[a.asname or a.name] = ("cls", a.name)
    return out


def _raw_time_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    binds = _bindings(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            b = binds.get(func.value.id)
            if b is None:
                continue
            kind, origin = b
            if kind == "mod" and origin == "time" and func.attr in _TIME_MOD_ATTRS:
                hits.append(f"L{node.lineno} {func.value.id}.{func.attr}")
            elif kind == "cls" and origin == "datetime" and func.attr in _DATETIME_CLS_ATTRS:
                hits.append(f"L{node.lineno} {func.value.id}.{func.attr}")
            elif kind == "cls" and origin == "date" and func.attr in _DATE_CLS_ATTRS:
                hits.append(f"L{node.lineno} {func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name):
            b = binds.get(func.id)
            if b is not None and b[0] == "func" and b[1] in _TIME_MOD_ATTRS:
                hits.append(f"L{node.lineno} {func.id}()")
    return hits


def test_no_raw_time_outside_clock() -> None:
    offenders = {}
    for path in _PKG.rglob("*.py"):
        rel = str(path.relative_to(_PKG))
        if rel == "clock.py" or rel in _ALLOWLIST:
            continue
        hits = _raw_time_calls(path)
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        f"current-time reads outside clock.py: {offenders}. Take a Clock (or "
        f"clock.SYSTEM_CLOCK) instead of time.time()/datetime.now()/time.sleep()/etc."
    )


def test_allowlist_only_names_real_offenders() -> None:
    """Keep the allowlist honest: a clock-clean file must be REMOVED, not parked."""
    stale = {rel for rel in _ALLOWLIST if not _raw_time_calls(_PKG / rel)}
    assert not stale, f"allowlist names clock-clean files — remove them: {stale}"


def test_scan_catches_aliased_and_from_import_dodges(tmp_path: Path) -> None:
    """Self-test: the two forms a real review found slipping past the naive match
    (aliased module, missing attr) MUST now be caught, and pure conversions/method
    formatting MUST NOT be."""
    caught = tmp_path / "caught.py"
    caught.write_text(
        "import time as _t\n"
        "from time import sleep as _s\n"
        "from datetime import datetime as _dt\n"
        "def f():\n"
        "    _t.strftime('%Y')\n"  # aliased module + strftime (the vcs_state dodge)
        "    _t.sleep(1)\n"  # aliased module sleep (the api.py dodge)
        "    _s(2)\n"  # bare from-imported sleep
        "    _dt.now()\n"  # aliased datetime class
    )
    assert len(_raw_time_calls(caught)) == 4

    clean = tmp_path / "clean.py"
    clean.write_text(
        "from datetime import datetime\n"
        "from agent_runner.clock import SYSTEM_CLOCK\n"
        "def g(ts):\n"
        "    SYSTEM_CLOCK.now_utc().strftime('%Y')\n"  # method on clock datetime — pure
        "    datetime.fromisoformat(ts)\n"  # parse — pure
        "    datetime.fromtimestamp(0)\n"  # conversion — pure
    )
    assert _raw_time_calls(clean) == []
