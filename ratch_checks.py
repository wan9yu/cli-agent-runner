"""ratch gates. One executor per fact; pytest keeps unmatched dialects.

Not yet: forbidden-literal (author-tool tokens already on NoAiSignatures +
CI; empty patterns are VACUOUS), no-internal-refs (docs/internal/ is a public
doc fence, not a notes prefix).
"""

from ratch.checks.ai_signatures import NoAiSignatures
from ratch.checks.autoclose import NoAutocloseKeywords
from ratch.checks.bdd_conventions import BddTestConventions
from ratch.checks.catalog_size import CatalogSize
from ratch.checks.circular_import import NoCircularImport
from ratch.checks.commit_heatmap import CommitHeatmap
from ratch.checks.conflict_markers import NoConflictMarkers
from ratch.checks.doc_cli import DocCliExamplesValid
from ratch.checks.first_person import NoFirstPerson
from ratch.checks.font_cdn import NoExternalFontCdn
from ratch.checks.hash_named_test import NoHashNamedTest
from ratch.checks.injected_clock import InjectedClock
from ratch.checks.loc_cap import LocCap
from ratch.checks.manifest_purity import ManifestPurity
from ratch.checks.pytest_skip import NoPytestSkip
from ratch.checks.reassurance import NoReassuranceWords
from ratch.checks.repo_root_ssot import RepoRootSSot
from ratch.checks.todo_issue import TodoHasIssueRef
from ratch.checks.vacuous_assert import NoVacuousAssert

CHECKS = [
    NoConflictMarkers(),
    BddTestConventions(prefix="test_", blank_blocks=0),
    NoHashNamedTest(),
    NoPytestSkip(paths=("tests/invariants/**/*.py",)),
    LocCap(),
    InjectedClock(
        clock_paths=("agent_runner/clock.py",),
        paths=("agent_runner/**/*.py",),
    ),
    RepoRootSSot(ssot="tests/_test_helpers.py"),
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
    NoCircularImport(package="agent_runner"),
    TodoHasIssueRef(),
    NoReassuranceWords(),
    NoAutocloseKeywords(),
    ManifestPurity(),
    NoExternalFontCdn(),
    NoFirstPerson(),
    DocCliExamplesValid(),
    CatalogSize(),
    CommitHeatmap(),
]
