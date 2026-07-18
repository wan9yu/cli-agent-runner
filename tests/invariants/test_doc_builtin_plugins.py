"""Invariant: docs/plugins.md's built-in post_round_hooks section is complete.

pyproject's entry-points table is the SSOT for what ships. The section's count
and its per-plugin subsections both drifted when codewhale landed in 0.1.41 —
a plugin author reading this page cannot know codewhale_error_detector exists,
nor that `[plugins] disable` accepts its name.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _builtin_post_round_hook_names() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["entry-points"]["agent_runner.post_round_hooks"])


def test_given_plugins_doc_when_scanned_then_lists_every_builtin_post_round_hook() -> None:
    names = _builtin_post_round_hook_names()
    text = (REPO / "docs/plugins.md").read_text(encoding="utf-8")
    section = text.split("## Built-in post_round_hooks", 1)[-1].split("\n## ", 1)[0]

    m = re.search(r"ships (\d+) built-in", section)
    assert m, (
        "docs/plugins.md no longer states a built-in post_round_hooks count as a "
        "digit (reworded? update this guard)"
    )
    assert int(m.group(1)) == len(names), (
        f"docs/plugins.md claims {m.group(1)} built-in post_round_hooks; "
        f"pyproject registers {len(names)}: {sorted(names)}"
    )

    missing = {n for n in names if f"`{n}`" not in section}
    assert not missing, (
        f"docs/plugins.md's built-in post_round_hooks section never names "
        f"{sorted(missing)} — a plugin author cannot discover it"
    )
