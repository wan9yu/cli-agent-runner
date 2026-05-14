"""Invariant: docs/events.md must exist and state the version-discriminator contract."""

from __future__ import annotations

from pathlib import Path


def test_given_events_doc_when_read_then_contract_phrases_present() -> None:
    """docs/events.md must contain the schema-versioning contract phrases."""
    doc = Path(__file__).resolve().parent.parent.parent / "docs" / "events.md"
    assert doc.exists(), f"docs/events.md missing: {doc}"
    text = doc.read_text(encoding="utf-8")
    assert "version discriminator" in text
    assert "append-only" in text
