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


def test_every_migration_describes_with_no_config_in_hand() -> None:
    assert MIGRATIONS, "MIGRATIONS registry emptied"  # vacuity-guard
    for m in MIGRATIONS:
        desc = _describe(m, {})
        assert isinstance(desc, str) and desc.strip(), (
            f"{m.describe!r} produced an empty/non-string description for docgen's {{}} call"
        )
