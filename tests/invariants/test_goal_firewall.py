"""The advisory/kill firewall.

The goal-steering surface (the ``goal_check``/``goal_assessment`` event
kinds, ``GOAL_CHECK``/``GOAL_ASSESSMENT`` in ``events.py``, and
``agent_runner.goal`` itself) is advisory-only: it must be structurally
unable to reach any kill/give-up decision. Rather than hand-listing every
kill-path module (a list that silently goes stale as the kill path grows),
this scans EVERY ``agent_runner/*.py`` module NOT on the small, explicit
goal-aware allowlist and asserts none of them references the surface at all
-- so a future module can only gain that knowledge by being added to the
allowlist, in the open, in a diff someone reviews.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"

# Modules permitted to know the goal-steering surface exists: the
# assessor/ledger/executor itself, its sole caller, the event-kind SSOT
# where GOAL_CHECK/GOAL_ASSESSMENT are declared, and the config layer that
# parses/types the [goal] table (cfg.goal threading is firewall-safe -- it
# carries budget/ledger-path data, never a kill decision). Everything else
# in agent_runner/ must stay goal-blind.
_PERMITTED_RELATIVE_PATHS = frozenset({"goal.py", "runner.py", "events.py"})
_PERMITTED_PREFIXES = ("config/",)

# Word-boundary, not naive substring: agent_runner/_serve_policy.py's
# docstring carries the PERMITTED identifier `goal_checks_allowance_s`
# (config-side budget threading), whose substring IS `goal_check` -- a plain
# `"goal_check" in literal` would false-positive on it. `s` right after
# `goal_check` is a word char, so `\bgoal_check\b` correctly does not match.
_GOAL_CHECK_RE = re.compile(r"\bgoal_check\b")
_GOAL_ASSESSMENT_RE = re.compile(r"\bgoal_assessment\b")

# GOAL_CHECK/GOAL_ASSESSMENT as a Name or Attribute reference (e.g. an
# `events.GOAL_CHECK` lookup, or a `from ... import GOAL_CHECK` use site).
# Exact match only -- `_GOAL_CHECK_ALLOWED_FIELDS` etc. are distinct
# identifiers and must not trip this.
_FLAGGED_IDENTIFIERS = frozenset({"GOAL_CHECK", "GOAL_ASSESSMENT"})


def _is_permitted(rel_posix: str) -> bool:
    return rel_posix in _PERMITTED_RELATIVE_PATHS or rel_posix.startswith(_PERMITTED_PREFIXES)


def _goal_firewall_offenders(base: Path) -> list[str]:
    """Scan every ``.py`` file under ``base`` that is NOT on the permitted
    allowlist for a reference to the goal-steering surface: a
    goal_check/goal_assessment string literal (word-boundary matched), a
    GOAL_CHECK/GOAL_ASSESSMENT Name/Attribute reference, or an
    ``agent_runner.goal`` import edge (``import agent_runner.goal``,
    ``from agent_runner.goal import ...``, or ``from agent_runner import
    goal``). Returns ``"<path>: <reason>"`` strings, AST-based so a
    ``patch("agent_runner.goal.foo")`` STRING in an unrelated test is never
    mistaken for a real import -- only nodes the parser itself classifies as
    Import/ImportFrom count as import edges."""
    offenders: list[str] = []
    scanned = 0
    for f in sorted(base.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        rel = f.relative_to(base).as_posix()
        if _is_permitted(rel):
            continue
        scanned += 1
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _GOAL_CHECK_RE.search(node.value):
                    offenders.append(f"{rel}: goal_check string literal {node.value!r}")
                elif _GOAL_ASSESSMENT_RE.search(node.value):
                    offenders.append(f"{rel}: goal_assessment string literal {node.value!r}")
            elif isinstance(node, ast.Name) and node.id in _FLAGGED_IDENTIFIERS:
                offenders.append(f"{rel}: {node.id} reference")
            elif isinstance(node, ast.Attribute) and node.attr in _FLAGGED_IDENTIFIERS:
                offenders.append(f"{rel}: .{node.attr} reference")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "agent_runner.goal" or node.module.startswith(
                    "agent_runner.goal."
                ):
                    offenders.append(f"{rel}: from {node.module} import ...")
                elif node.module == "agent_runner" and any(
                    alias.name == "goal" for alias in node.names
                ):
                    offenders.append(f"{rel}: from agent_runner import goal")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "agent_runner.goal" or alias.name.startswith(
                        "agent_runner.goal."
                    ):
                        offenders.append(f"{rel}: import {alias.name}")

    # vacuity-guard
    assert scanned > 0, "no non-permitted agent_runner modules scanned"
    return offenders


def test_goal_steering_should_stay_off_the_kill_path_when_scanned() -> None:
    """goal.py, its sole caller runner.py, the GOAL_CHECK/GOAL_ASSESSMENT
    kind constants in events.py, and the config/ layer that types/parses
    [goal] are the ONLY places in agent_runner/ allowed to know the
    goal-steering surface exists. Every other module -- in particular
    anything on or near the kill path (monitor.py, _lifecycle.py,
    agent_runtime.py, service_unit.py, remote_relay.py, ...) -- must be
    structurally unable to reference it, so an advisory signal can never be
    wired into a stop/kill decision."""
    offenders = _goal_firewall_offenders(PKG)

    assert offenders == [], (
        "goal-steering surface referenced outside the permitted allowlist "
        "-- the advisory/kill firewall is broken:\n" + "\n".join(offenders)
    )


def test_goal_firewall_scan_should_flag_a_synthetic_leak_when_scanned(tmp_path: Path) -> None:
    """Non-vacuity proof for the scan above: a module that spells the
    goal_check/goal_assessment kind literal, references GOAL_CHECK, or
    imports agent_runner.goal (either import form) must be caught -- and the
    permitted `goal_checks_allowance_s` identifier (whose substring IS
    goal_check, exactly the _serve_policy.py case) plus `cfg.goal` attribute
    access must NOT be, proving the scan is word-boundary/exact-match, not
    naive substring."""
    (tmp_path / "bad_check_literal.py").write_text('KIND = "goal_check"\n')
    (tmp_path / "bad_assessment_literal.py").write_text('KIND = "goal_assessment"\n')
    (tmp_path / "bad_name_ref.py").write_text(
        "from agent_runner.events import GOAL_CHECK\n\n\ndef f():\n    return GOAL_CHECK\n"
    )
    (tmp_path / "bad_attr_ref.py").write_text(
        "import agent_runner.events as events\n\n\ndef f():\n    return events.GOAL_ASSESSMENT\n"
    )
    (tmp_path / "bad_from_import.py").write_text("from agent_runner.goal import assess_treadmill\n")
    (tmp_path / "bad_plain_import.py").write_text("import agent_runner.goal\n")
    (tmp_path / "bad_submodule_import.py").write_text(
        "from agent_runner import goal\n\n\ndef f():\n    return goal.run_goal_checks\n"
    )
    (tmp_path / "good_allowance.py").write_text(
        "def f(goal_checks_allowance_s: int = 0) -> int:\n    return goal_checks_allowance_s\n"
    )
    (tmp_path / "good_docstring.py").write_text(
        '"""Folds goal_checks_allowance_s into the outer ceiling for a slow '
        'but bounded goal-check.\n"""\n'
    )
    (tmp_path / "good_attr.py").write_text(
        "def f(cfg):\n"
        "    if cfg.goal is not None:\n"
        "        return cfg.goal.ledger\n"
        "    return None\n"
    )

    offenders = _goal_firewall_offenders(tmp_path)

    assert any("bad_check_literal.py" in o for o in offenders)
    assert any("bad_assessment_literal.py" in o for o in offenders)
    assert any("bad_name_ref.py" in o for o in offenders)
    assert any("bad_attr_ref.py" in o for o in offenders)
    assert any("bad_from_import.py" in o for o in offenders)
    assert any("bad_plain_import.py" in o for o in offenders)
    assert any("bad_submodule_import.py" in o for o in offenders)
    assert not any("good_allowance.py" in o for o in offenders)
    assert not any("good_docstring.py" in o for o in offenders)
    assert not any("good_attr.py" in o for o in offenders)
    assert len(offenders) == 7
