"""ratch gates. One executor per fact; pytest keeps unmatched dialects.

Not yet: BDD (~495 missing _when_, 16 ``or`` segments), whole-repo loc-cap
(four test files >1000), first-person, forbidden-literal, ratch internal-refs
(different forbidden set), injected-clock (sleep vs now), tests-repo-root-ssot.
"""

from ratch.checks.ai_signatures import NoAiSignatures
from ratch.checks.autoclose import NoAutocloseKeywords
from ratch.checks.catalog_size import CatalogSize
from ratch.checks.circular_import import NoCircularImport
from ratch.checks.commit_heatmap import CommitHeatmap
from ratch.checks.conflict_markers import NoConflictMarkers
from ratch.checks.doc_cli import DocCliExamplesValid
from ratch.checks.font_cdn import NoExternalFontCdn
from ratch.checks.hash_named_test import NoHashNamedTest
from ratch.checks.loc_cap import LocCap
from ratch.checks.manifest_purity import ManifestPurity
from ratch.checks.pytest_skip import NoPytestSkip
from ratch.checks.reassurance import NoReassuranceWords
from ratch.checks.todo_issue import TodoHasIssueRef
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
    NoCircularImport(package="agent_runner"),
    TodoHasIssueRef(),
    NoReassuranceWords(),
    NoAutocloseKeywords(),
    ManifestPurity(),
    NoExternalFontCdn(),
    DocCliExamplesValid(),
    CatalogSize(),
    CommitHeatmap(),
]
