"""Invariant: docs/plugins.md documents every `agent_usage_recorded` field.

The payload reference used to live in a per-version migration guide — an API
reference in a migration costume. It moved into docs/plugins.md next to the
emitting detector.
Pin it to the source of truth — the keyword-only parameters of
`emit_agent_usage_recorded` — so the doc table can never drift from the signature.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from agent_runner._emit import emit_agent_usage_recorded

REPO = Path(__file__).resolve().parents[2]
PLUGINS_DOC = REPO / "docs/plugins.md"


def _keyword_only_params() -> list[str]:
    sig = inspect.signature(emit_agent_usage_recorded)
    return [name for name, p in sig.parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY]


def test_given_usage_event_when_documented_then_every_field_present() -> None:
    text = PLUGINS_DOC.read_text(encoding="utf-8")
    missing = [name for name in _keyword_only_params() if f"`{name}`" not in text]
    assert not missing, (
        "docs/plugins.md omits agent_usage_recorded payload fields "
        f"(keyword-only params of emit_agent_usage_recorded): {missing}"
    )
