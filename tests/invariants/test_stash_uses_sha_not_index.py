"""§9 IMMUTABLE — vcs_state must never name a stash by stash@{N} index.

R820 + orphan-stash-archive-2026-04-23 lesson: concurrent auto-stash shifts
indices, so an index captured at one moment names a different stash at the
next. Stashes are identified by the SHA that stash_orphan returns.
"""

from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent.parent / "agent_runner"


def test_given_vcs_state_stash_calls_when_scanned_then_no_stash_at_brace_index() -> None:
    """Forbid `git stash drop stash@{N}` / `git stash pop stash@{N}` patterns."""
    text = (PKG / "vcs_state.py").read_text()
    pattern = re.compile(r'"stash@\{[0-9]+\}"')
    matches = pattern.findall(text)
    assert matches == [], f"vcs_state.py uses stash@{{N}} index pattern: {matches}"
