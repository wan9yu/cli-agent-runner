"""ratch gates. One executor per fact; pytest keeps dialects ratch cannot match.

BDD waits on ~495 names missing ``_when_`` (and 16 with an ``or`` segment).
Whole-repo loc-cap waits on four test files over 1000 lines.
"""

from ratch.checks.ai_signatures import NoAiSignatures
from ratch.checks.catalog_size import CatalogSize
from ratch.checks.commit_heatmap import CommitHeatmap
from ratch.checks.conflict_markers import NoConflictMarkers
from ratch.checks.hash_named_test import NoHashNamedTest
from ratch.checks.loc_cap import LocCap
from ratch.checks.pytest_skip import NoPytestSkip
from ratch.checks.vacuous_assert import NoVacuousAssert

CHECKS = [
    NoConflictMarkers(),
    NoHashNamedTest(),
    NoPytestSkip(paths=("tests/invariants/**/*.py",)),
    LocCap(paths=("agent_runner/**/*.py",), max_lines=1000),
    NoVacuousAssert(),
    NoAiSignatures(
        sources=("log", "tags", "files"),
        files=("CHANGELOG.md",),
        patterns=(
            r"Co-Authored-By:",
            r"Generated with Claude",
            r"Generated with \[Cursor\]",
            "\N{ROBOT FACE}",
            r"noreply@anthropic\.com",
        ),
    ),
    CatalogSize(),
    CommitHeatmap(),
]
