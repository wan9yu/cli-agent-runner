"""Invariant: every MIGRATIONS entry renders standalone, with no config in hand.

``agent_runner/_docgen.py``'s static registry render (``_render_migrate_transforms``,
which feeds ``docs/commands.md``'s generated migrate-transforms table) calls
``_describe(m, {})`` for EVERY entry in ``MIGRATIONS`` — there is no parsed config at
doc-generation time. A ``describe`` callable that assumes a populated ``parsed`` dict
(e.g. indexing a key docgen's empty ``{}`` never supplies) would crash or render a
blank/garbled row, and nothing else in the suite calls every entry this way.
"""

from __future__ import annotations

from agent_runner.migrations import MIGRATIONS, _describe


def test_every_migration_should_describe_with_no_config_in_hand_when_invoked() -> None:
    assert MIGRATIONS, "MIGRATIONS registry emptied"  # vacuity-guard

    empty = [
        repr(m.describe)
        for m in MIGRATIONS
        if not isinstance(_describe(m, {}), str) or not str(_describe(m, {})).strip()
    ]

    assert empty == [], f"docgen {{}} describe produced empty/non-string: {empty}"
