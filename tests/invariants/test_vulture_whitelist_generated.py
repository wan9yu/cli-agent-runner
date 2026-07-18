"""CI gate — .vulture-whitelist.py must equal a fresh generation.

Mirrors test_docs_generated.py: the whitelist is generated from every
``@dataclass`` in ``agent_runner/`` (its SSOT), so a new field that isn't
regenerated fails here. Run ``./build.sh vulture-whitelist`` to fix.
"""

from __future__ import annotations

from tests.generate_vulture_whitelist import WHITELIST_PATH, generate


def test_given_whitelist_when_regenerated_then_matches_on_disk() -> None:
    committed = WHITELIST_PATH.read_text(encoding="utf-8")
    fresh = generate()
    assert committed == fresh, (
        ".vulture-whitelist.py is out of date — a @dataclass field changed without "
        "regenerating. Run `./build.sh vulture-whitelist` and commit."
    )
