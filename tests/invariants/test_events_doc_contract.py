"""Invariant: docs/events.md must exist and state the version-discriminator contract."""

from __future__ import annotations

from pathlib import Path


def test_events_doc_should_contain_contract_phrases_when_read() -> None:
    doc = Path(__file__).resolve().parent.parent.parent / "docs" / "events.md"
    assert doc.exists(), f"docs/events.md missing: {doc}"

    text = doc.read_text(encoding="utf-8")

    assert "version discriminator" in text
    assert "append-only" in text
