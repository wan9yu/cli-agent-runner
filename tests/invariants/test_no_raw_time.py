"""Invariant: raw wall-clock/monotonic/sleep primitives live ONLY in clock.py.

Every other module must take a ``Clock`` (or read ``clock.SYSTEM_CLOCK``) so time
is injectable — one ``FakeClock`` pins epoch/monotonic/sleep/now for a whole test
instead of a monkeypatch per call site. See ``agent_runner/clock.py``.

``_ALLOWLIST`` names files not yet migrated onto the clock; it shrinks to empty as
the migration lands, and a new raw-time call in a migrated file fails here. Adding
a file back to the allowlist is a regression — migrate it instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"

# (object, attribute) pairs that read the current time or block — the things a
# Clock abstracts. Parsing (fromisoformat/fromtimestamp) is pure and NOT listed.
_FORBIDDEN = {
    ("time", "time"),
    ("time", "monotonic"),
    ("time", "sleep"),
    ("datetime", "now"),
    ("datetime", "utcnow"),
}

# clock.py is the sanctioned home. Migration complete — the allowlist is empty:
# every other module reads time through a Clock. Adding an entry here is a
# regression; migrate the file onto clock.SYSTEM_CLOCK instead.
_ALLOWLIST: set[str] = set()


def _raw_time_calls(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    n = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and (node.func.value.id, node.func.attr) in _FORBIDDEN
        ):
            n += 1
    return n


def test_no_raw_time_outside_clock() -> None:
    offenders = {}
    for path in _PKG.rglob("*.py"):
        rel = str(path.relative_to(_PKG))
        if rel == "clock.py" or rel in _ALLOWLIST:
            continue
        if _raw_time_calls(path):
            offenders[rel] = _raw_time_calls(path)
    assert not offenders, (
        f"raw time calls outside clock.py: {offenders}. Take a Clock (or "
        f"clock.SYSTEM_CLOCK) instead of time.time()/datetime.now()/time.sleep()."
    )


def test_allowlist_only_names_real_offenders() -> None:
    """Keep the allowlist honest: once a file is clock-clean, it must be REMOVED
    from the allowlist (not left as dead scaffolding)."""
    stale = {rel for rel in _ALLOWLIST if not _raw_time_calls(_PKG / rel)}
    assert not stale, f"allowlist names clock-clean files — remove them: {stale}"
