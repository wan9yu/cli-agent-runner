"""§9 IMMUTABLE — `git stash drop/pop` must use SHA, not stash@{N} index.

R820 + orphan-stash-archive-2026-04-23 lesson: concurrent auto-stash shifts
indices, off-by-one drops the wrong stash. Lock by SHA (immutable).
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


def test_given_drop_stash_signature_when_inspected_then_takes_sha_string() -> None:
    text = (PKG / "vcs_state.py").read_text()
    assert "def drop_stash(repo: Path, sha: str)" in text, (
        "drop_stash must take sha:str, not int index"
    )
    assert "def pop_stash(repo: Path, sha: str)" in text, (
        "pop_stash must take sha:str, not int index"
    )
