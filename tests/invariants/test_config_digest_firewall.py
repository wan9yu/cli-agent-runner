"""config_digest is a round_start label. The kill/give-up path must not read it."""

from __future__ import annotations

from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"

_PERMITTED = frozenset({"config/digest.py", "runner.py"})


def test_kill_and_give_up_modules_should_not_name_config_digest() -> None:
    offenders: list[str] = []
    for path in PKG.rglob("*.py"):
        rel = path.relative_to(PKG).as_posix()
        if rel in _PERMITTED or rel.startswith("config/"):
            continue
        text = path.read_text(encoding="utf-8")
        if "config_digest" in text:
            offenders.append(rel)
    assert not offenders, f"config_digest leaked into kill/give-up surface: {offenders}"
