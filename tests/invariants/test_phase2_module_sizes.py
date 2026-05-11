"""Phase 2 module-size guards.

Two kinds of caps:
- HARD CAPS (architectural): serve_cmd <=60 (thin loop), cli/*_cmd <=80 (formatter only).
  These enforce the design intent that those layers stay thin.
- RATCHET CAPS (drift prevention): api.py <=320, monitor.py <=460.
  Sized to current implementation. They prevent future growth without explicit
  refactor; they should be DECREASED when production code shrinks, never increased.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


@pytest.mark.parametrize("rel,limit", [
    ("cli/serve_cmd.py", 60),    # HARD cap — thin loop, signal-trapping only
    ("api.py", 320),              # ratchet — current 315; reduce only via refactor
    ("monitor.py", 460),          # ratchet — current 451; 9 detectors are most of it
    ("defenses.py", 180),         # cap with ~70 LOC headroom for new defenses
])
def test_given_phase2_module_when_counted_then_under_limit(rel: str, limit: int) -> None:
    """Module size guards. ``serve_cmd`` is HARD CAP (architectural).
    Others are RATCHET CAPS — reduce them when modules shrink, never raise.
    """
    f = PKG / rel
    assert f.exists(), f"{rel} missing"
    n = sum(1 for _ in f.read_text().splitlines())
    assert n <= limit, f"{rel} has {n} lines, limit {limit}"


def test_given_each_cli_cmd_file_when_counted_then_under_eighty() -> None:
    cli_dir = PKG / "cli"
    offenders: list[tuple[str, int]] = []
    for f in cli_dir.glob("*_cmd.py"):
        n = sum(1 for _ in f.read_text().splitlines())
        if n > 80:
            offenders.append((f.name, n))
    assert offenders == [], f"cli cmd files exceed 80 LOC: {offenders}"
