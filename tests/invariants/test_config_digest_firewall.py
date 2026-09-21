"""config_digest is a round_start label. The kill/give-up path must not read it."""

from __future__ import annotations

import ast

from tests._test_helpers import ROOT

PKG = ROOT / "agent_runner"


def test_kill_and_give_up_modules_should_not_name_config_digest_when_invoked() -> None:
    offenders: list[str] = []

    for path in PKG.rglob("*.py"):
        rel = path.relative_to(PKG).as_posix()
        if rel == "runner.py" or rel.startswith("config/"):
            continue
        if "config_digest" in path.read_text(encoding="utf-8"):
            offenders.append(rel)

    assert not offenders, f"config_digest leaked into kill/give-up surface: {offenders}"


def test_runner_py_should_not_read_config_digest_identifier_when_invoked() -> None:
    tree = ast.parse((PKG / "runner.py").read_text(encoding="utf-8"))

    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "config_digest":
            hits.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "config_digest":
            hits.append(node.lineno)

    assert not hits, f"runner.py reads config_digest at lines {hits}"
