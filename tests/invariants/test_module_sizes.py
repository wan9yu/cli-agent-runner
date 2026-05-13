"""Single ceiling on production module sizes.

Catches the actual signal worth catching — "this file got long enough that
a split is overdue" — without the per-release ratchet-bumping bookkeeping
the old parameterised version generated.
"""

from __future__ import annotations

from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"
LIMIT = 1000


def test_given_production_module_when_counted_then_under_thousand_lines() -> None:
    offenders: list[tuple[str, int]] = []
    for path in PKG.rglob("*.py"):
        n = sum(1 for _ in path.read_text(encoding="utf-8").splitlines())
        if n > LIMIT:
            offenders.append((str(path.relative_to(PKG)), n))
    assert offenders == [], f"modules exceed {LIMIT} LOC; split overdue: {offenders}"
