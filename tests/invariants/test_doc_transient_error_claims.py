"""Invariants: the transient-error doc surface must match the code.

Both claims guarded here were live instructions to external consumers: use a
removed config field, or subscribe to an event that raises on emit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import make_toml_with_sections

REPO = Path(__file__).resolve().parents[2]

# The operator + plugin-author reference pages. docs/long-running-agents.md and
# docs/migrations/ are excluded: they describe the removal, which is correct.
_REFERENCE_DOCS = ("docs/runbook.md", "docs/plugins.md")


def _doc_hits(needle: str) -> list[str]:
    out: list[str] = []
    for fname in _REFERENCE_DOCS:
        for lineno, line in enumerate((REPO / fname).read_text(encoding="utf-8").splitlines(), 1):
            if needle in line:
                out.append(f"{fname}:{lineno}: {line.strip()}")
    return out


def test_given_reference_docs_when_scanned_then_no_removed_event_kinds() -> None:
    """rate_limit_rejected was removed in 0.1.29 — emit() raises on it now."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    assert "rate_limit_rejected" not in KNOWN_EVENT_KINDS, (
        "premise changed: rate_limit_rejected is registered again"
    )
    failures = _doc_hits("rate_limit_rejected")
    assert not failures, (
        "reference docs name an event kind that can never be emitted:\n" + "\n".join(failures)
    )


def test_given_reference_docs_when_scanned_then_removed_config_alias_not_offered(
    tmp_path: Path,
) -> None:
    """runtime.rate_limit_action raises ConfigError — no doc may offer it."""
    from agent_runner.config import ConfigError, load_config

    cfg_path = make_toml_with_sections(tmp_path, runtime_extra='rate_limit_action = "stop"\n')
    with pytest.raises(ConfigError):
        load_config(cfg_path)

    failures = _doc_hits("rate_limit_action")
    assert not failures, (
        "reference docs mention a config field that raises ConfigError at load:\n"
        + "\n".join(failures)
    )
