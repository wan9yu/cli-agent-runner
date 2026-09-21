"""Invariant: ``_procwait.py``'s wait paths must let ``KeyboardInterrupt``
propagate.

``agent_runtime._kill_pgroup``'s grace-window SIGKILL escalation depends on
this: a re-entrant SIGTERM lands as a fresh ``KeyboardInterrupt`` (round_cmd's
handler converts every SIGTERM into one for the process's whole life), and
``_kill_pgroup`` catches it around its own ``wait_exit`` call to retry against
the SAME absolute deadline -- see ``tests/unit/test_kill_pgroup_shielding.py``
for that property, driven with a ``FakeClock``. That shield only works because
``_procwait`` itself never intercepts the interrupt first. A future
``except BaseException``, ``except KeyboardInterrupt``, or bare ``except:``
wrapping the fast-path ``select.select`` (``wait_exit``) or the poll fallback
loop (``_wait_exit_by_polling``) would silently swallow it there instead --
turning the shield into dark code that never gets a chance to retry, while
every EXISTING test (which drives the fallback with a ``FakeClock`` that never
raises through ``_procwait`` itself) would keep passing regardless. This is
exactly the class of gap the v0.3.3 release named: a review that checks only
the mechanism/arg, not the end-to-end property, can miss it.

AST-scans the WHOLE module (not just ``wait_exit``/``_wait_exit_by_polling``
by name) so the guard can't be dodged by moving the catch to a new function.
Only specific exception types (``OSError`` etc.) are allowed handlers.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._test_helpers import ROOT

_PROCWAIT = ROOT / "agent_runner" / "_procwait.py"

_FORBIDDEN_HANDLER_NAMES = {"BaseException", "KeyboardInterrupt"}


def _shield_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            hits.append(f"L{node.lineno} bare except:")
            continue
        types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        for t in types:
            if isinstance(t, ast.Name) and t.id in _FORBIDDEN_HANDLER_NAMES:
                hits.append(f"L{node.lineno} except {t.id}")

    return hits


def test_procwait_should_never_catch_keyboardinterrupt_else_baseexception_when_invoked() -> None:
    assert _PROCWAIT.is_file(), "agent_runner/_procwait.py not found"  # vacuity-guard

    hits = _shield_violations(_PROCWAIT)

    assert not hits, (
        f"_procwait.py catches {hits} -- wait_exit must let KeyboardInterrupt propagate so "
        f"_kill_pgroup's grace-shield can retry -- CPython#127049 / v0.3.3 dark-code guard."
    )


def test_scanner_should_flag_reintroduced_shield_break_when_scanned(tmp_path: Path) -> None:
    """Non-vacuity proof: plant each forbidden handler shape and confirm the
    scanner catches it; a file with only ``except OSError`` (the real,
    allowed handler in ``exit_fd``) must pass clean."""
    ki_offender = tmp_path / "ki_offender.py"
    ki_offender.write_text(
        "def f():\n    try:\n        pass\n    except KeyboardInterrupt:\n        pass\n"
    )
    assert _shield_violations(ki_offender), "scanner failed to flag except KeyboardInterrupt"

    base_offender = tmp_path / "base_offender.py"
    base_offender.write_text(
        "def f():\n    try:\n        pass\n    except BaseException:\n        pass\n"
    )
    assert _shield_violations(base_offender), "scanner failed to flag except BaseException"

    bare_offender = tmp_path / "bare_offender.py"
    bare_offender.write_text("def f():\n    try:\n        pass\n    except:\n        pass\n")
    assert _shield_violations(bare_offender), "scanner failed to flag a bare except:"

    tuple_offender = tmp_path / "tuple_offender.py"
    tuple_offender.write_text(
        "def f():\n    try:\n        pass\n    except (OSError, KeyboardInterrupt):\n        pass\n"
    )
    assert _shield_violations(tuple_offender), "scanner failed to flag a tuple-form handler"

    clean = tmp_path / "clean.py"
    clean.write_text("def f():\n    try:\n        pass\n    except OSError:\n        pass\n")
    assert not _shield_violations(clean), "scanner false-positived on the real except OSError"
