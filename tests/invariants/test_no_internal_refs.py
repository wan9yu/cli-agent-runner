"""Invariant: no internal-only codenames leak into tracked files.

This repo's test fixtures, docstrings, and incident-postmortem framing used to
carry an internal deployment codename, an internal project codename, and
planning-shorthand wording. Scrubbed once (0.2.15), this test keeps them
scrubbed. "ouroboros" is intentionally NOT forbidden: it is shipped
terminology used in ``agent_runner/_throttle.py`` and
``agent_runner/_monitor_registry.py``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_THIS = Path(__file__).relative_to(_REPO).as_posix()
# "inception" is an ordinary English word but is a deliberate internal codename here — forbid it.
_FORBIDDEN = re.compile(r"argus|inception|\bplan[-_ ]b\b", re.IGNORECASE)
_SKIP = {_THIS, ".vulture-whitelist.py"}  # self-exclude: this file's own regex text matches
_SCAN_SUFFIXES = (".py", ".md", ".toml", ".cfg", ".txt", ".zh.md")


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout
    return [p for p in out.splitlines() if p.endswith(_SCAN_SUFFIXES) and p not in _SKIP]


def test_given_tracked_files_when_scanned_then_no_internal_codenames() -> None:
    hits = []
    for rel in _tracked_files():
        text = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _FORBIDDEN.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:80]}")
    assert not hits, "internal codenames leaked into tracked files:\n" + "\n".join(hits)
