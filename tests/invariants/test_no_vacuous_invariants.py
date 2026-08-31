"""Meta-invariant: corpus-scanning invariants must not pass vacuously.

An invariant that loops over a *discovered* corpus (AST walk, glob, doc scan) and
only asserts inside the loop passes trivially when the corpus is empty — a scan that
silently stops finding its targets then reads as "clean". Each such invariant must
carry an explicit non-emptiness guard, tagged ``# vacuity-guard`` so this meta-check
(and a human) can confirm it exists.

Registry, not auto-detection: an explicit list avoids false-positives on invariants
that loop over hardcoded literals (never empty) and documents which scans have been
certified. Add a file here when you add a corpus-scanning invariant."""

from __future__ import annotations

from pathlib import Path

INV = Path(__file__).resolve().parent

# Corpus-scanning invariants certified non-vacuous. Each must carry a
# ``# vacuity-guard`` comment on the assert that fails when its scanned corpus is empty.
CORPUS_SCANNING_INVARIANTS = (
    "test_module_boundaries.py",
    "test_atomic_write_enforced.py",
    "test_doc_test_pointers_exist.py",
    "test_stash_uses_sha_not_index.py",
    "test_monitor_self_exclusion.py",
    "test_event_int_coercion.py",
)


def test_registry_is_non_empty_and_files_exist() -> None:
    assert CORPUS_SCANNING_INVARIANTS, "registry emptied — the meta-check would be vacuous"
    for name in CORPUS_SCANNING_INVARIANTS:
        assert (INV / name).is_file(), f"{name} listed but missing"


def test_each_scanning_invariant_carries_a_vacuity_guard() -> None:
    missing = [
        name
        for name in CORPUS_SCANNING_INVARIANTS
        if "# vacuity-guard" not in (INV / name).read_text(encoding="utf-8")
    ]
    assert not missing, f"scanning invariants without a `# vacuity-guard`: {missing}"
