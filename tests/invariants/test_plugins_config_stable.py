"""Invariant: PluginsConfig fields published to plugin authors must not regress.

`disable` is not read by core: it is consumed through a local variable in
`load_config`, never as a stored attribute. It is a published contract —
CHANGELOG.md 0.1.12 publishes `cfg.plugins.disable: list[str]` as first-class.
Deleting it breaks the exact code the project told external authors to write.

`.raw` (the forward-compat catch-all for unknown `[plugins.*]` sub-keys) was
dropped in 0.3.9 as ecosystem-orphaned — zero plugins ever shipped a
`[plugins.*]` sub-key to read from it, so there was no published contract left
to protect. An unknown `[plugins]` key is now rejected at load time instead
(see `agent_runner.config._PLUGINS_ALLOWED_FIELDS`).
"""

from __future__ import annotations

from dataclasses import fields
from typing import get_type_hints

from agent_runner.config import PluginsConfig

REQUIRED_FIELDS: set[str] = {"disable"}


def test_plugins_config_should_have_published_fields_present_when_inspected() -> None:
    actual = {f.name for f in fields(PluginsConfig)}
    missing = REQUIRED_FIELDS - actual

    assert not missing, (
        f"PluginsConfig missing published fields: {missing}. "
        f"Plugin authors were directed to these by CHANGELOG 0.1.12 — do not remove."
    )


def test_plugins_config_published_types_should_match_when_inspected() -> None:
    hints = get_type_hints(PluginsConfig)

    assert hints["disable"] == list[str]
