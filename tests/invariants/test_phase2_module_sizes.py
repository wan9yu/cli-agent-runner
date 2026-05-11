"""Phase 2 module-size guards.

Two kinds of caps:
- HARD CAPS (architectural): serve_cmd <=60 (thin loop), cli/*_cmd <=90 (formatter only).
  These enforce the design intent that those layers stay thin.
- RATCHET CAPS (drift prevention): api.py <=335, monitor.py <=520.
  Sized to current implementation. They prevent future growth without explicit
  refactor; they should be DECREASED when production code shrinks, never increased.

Note: ratchet caps were reset on 2026-05-12 after a repo-wide ``ruff format``
sweep that expanded multi-arg signatures into per-arg lines. New baseline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


@pytest.mark.parametrize(
    "rel,limit",
    [
        ("cli/serve_cmd.py", 60),  # HARD cap — thin loop, signal-trapping only
        ("api.py", 335),  # ratchet — post-format baseline; reduce only via refactor
        ("monitor.py", 520),  # ratchet — post-format baseline; 9 detectors dominate
        ("defenses.py", 180),  # cap with ~70 LOC headroom for new defenses
    ],
)
def test_given_phase2_module_when_counted_then_under_limit(rel: str, limit: int) -> None:
    """Module size guards. ``serve_cmd`` is HARD CAP (architectural).
    Others are RATCHET CAPS — reduce them when modules shrink, never raise.
    """
    f = PKG / rel
    assert f.exists(), f"{rel} missing"
    n = sum(1 for _ in f.read_text().splitlines())
    assert n <= limit, f"{rel} has {n} lines, limit {limit}"


def test_given_each_cli_cmd_file_when_counted_then_under_ninety() -> None:
    cli_dir = PKG / "cli"
    offenders: list[tuple[str, int]] = []
    for f in cli_dir.glob("*_cmd.py"):
        n = sum(1 for _ in f.read_text().splitlines())
        if n > 90:
            offenders.append((f.name, n))
    assert offenders == [], f"cli cmd files exceed 90 LOC: {offenders}"
