"""Remainder time reads ratch ``injected-clock`` does not cover.

Ratch 0.1.4 binds stdlib ``time`` / ``datetime`` and flags ``ast.Call`` of
``time`` / ``sleep`` / ``monotonic`` / ``perf_counter`` and ``datetime.now`` /
``utcnow`` / ``today``. ``clock.sleep`` is the wait seam, not a leak.

This file keeps ``strftime`` / ``localtime`` / ``gmtime`` / ``*_ns`` /
``process_time`` and ``date.today`` (including ``datetime.date.today()``).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._test_helpers import ROOT

_PKG = ROOT / "agent_runner"

# ratch injected-clock already covers time/sleep/monotonic/perf_counter and
# datetime.now/utcnow/today. These extras are still this twin's fact.
_TIME_MOD_ATTRS = {
    "strftime",
    "localtime",
    "gmtime",
    "time_ns",
    "monotonic_ns",
    "process_time",
}
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
                    out[a.asname or a.name] = ("func", a.name)
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
            elif kind == "cls" and origin == "date" and func.attr in _DATE_CLS_ATTRS:
                hits.append(f"L{node.lineno} {func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name):
            b = binds.get(func.id)
            if b is not None and b[0] == "func" and b[1] in _TIME_MOD_ATTRS:
                hits.append(f"L{node.lineno} {func.id}()")
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
        ):
            b = binds.get(func.value.value.id)
            if b is not None and b[0] == "mod" and b[1] == "datetime":
                if func.value.attr == "date" and func.attr in _DATE_CLS_ATTRS:
                    name = f"{func.value.value.id}.{func.value.attr}.{func.attr}"
                    hits.append(f"L{node.lineno} {name}")
    return hits


def test_extra_time_calls_should_be_absent_outside_clock_when_invoked() -> None:
    offenders = {}
    scanned = 0
    for path in _PKG.rglob("*.py"):
        rel = str(path.relative_to(_PKG))
        if rel == "clock.py" or rel in _ALLOWLIST:
            continue
        scanned += 1
        hits = _raw_time_calls(path)
        if hits:
            offenders[rel] = hits

    assert scanned > 0, "no agent_runner/*.py modules scanned"  # vacuity-guard
    assert not offenders, (
        f"strftime/localtime/date.today outside clock.py: {offenders}. "
        "Take a Clock (or clock.SYSTEM_CLOCK); ratch covers time.sleep/datetime.now."
    )


def test_allowlist_should_only_name_real_offenders_when_invoked() -> None:
    """Keep the allowlist honest: a clock-clean file must be REMOVED, not parked."""
    stale = {rel for rel in _ALLOWLIST if not _raw_time_calls(_PKG / rel)}

    assert not stale, f"allowlist names clock-clean files — remove them: {stale}"


def test_extra_time_scan_should_catch_strftime_and_date_today_when_invoked(
    tmp_path: Path,
) -> None:
    """Self-test: extras ratch 0.1.4 does not cover must still be caught; stdlib
    sleep/now and Clock methods must not be."""
    caught = tmp_path / "caught.py"
    caught.write_text(
        "import time as _t\n"
        "from datetime import date as _d\n"
        "import datetime\n"
        "def f():\n"
        "    _t.strftime('%Y')\n"
        "    _d.today()\n"
        "    datetime.date.today()\n"
    )
    assert len(_raw_time_calls(caught)) == 3

    clean = tmp_path / "clean.py"
    clean.write_text(
        "from datetime import datetime\n"
        "import time\n"
        "from agent_runner.clock import SYSTEM_CLOCK\n"
        "def g(ts):\n"
        "    time.sleep(1)\n"  # ratch injected-clock, not this twin
        "    datetime.now()\n"  # ratch injected-clock, not this twin
        "    SYSTEM_CLOCK.now_utc().strftime('%Y')\n"
        "    datetime.fromisoformat(ts)\n"
    )
    assert _raw_time_calls(clean) == []
