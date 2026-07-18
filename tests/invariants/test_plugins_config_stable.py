"""Invariant: PluginsConfig fields published to plugin authors must not regress.

Neither field is read by core: `disable` is consumed through a local variable in
`load_config`, `raw` is never read at all. Both are published contracts:

* `raw`     — CHANGELOG.md 0.1.12 migration note directs plugin authors to read
              their own `[plugins.*]` sub-keys from `cfg.plugins.raw.get(...)`.
* `disable` — CHANGELOG.md 0.1.12 publishes `cfg.plugins.disable: list[str]` as
              first-class.

Deleting either breaks the exact code the project told external authors to
write. Core never reading `raw` is the correct shape for a forward-compat
catch-all, not evidence that it is dead.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, get_type_hints

from agent_runner.config import PluginsConfig

REQUIRED_FIELDS: set[str] = {"disable", "raw"}


def test_given_plugins_config_when_inspected_then_published_fields_present() -> None:
    actual = {f.name for f in fields(PluginsConfig)}
    missing = REQUIRED_FIELDS - actual
    assert not missing, (
        f"PluginsConfig missing published fields: {missing}. "
        f"Plugin authors were directed to these by CHANGELOG 0.1.12 — do not remove."
    )


def test_given_plugins_config_when_inspected_then_published_types_match() -> None:
    hints = get_type_hints(PluginsConfig)
    assert hints["disable"] == list[str]
    assert hints["raw"] == dict[str, Any]
