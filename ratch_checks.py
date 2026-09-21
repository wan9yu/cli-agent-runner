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
from ratch.checks.confined_import import ConfinedImport  # type: ignore[import-not-found]
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
    BddTestConventions(  # type: ignore[call-arg]
        prefix="test_",
        blank_blocks=3,
        forbid_comment_labels=("given", "when", "then", "arrange", "act", "assert"),
    ),
    NoHashNamedTest(),
    NoPytestSkip(paths=("tests/invariants/**/*.py",)),
    LocCap(),
    InjectedClock(  # type: ignore[call-arg]
        clock_paths=("agent_runner/clock.py",),
        paths=("agent_runner/**/*.py",),
        extra_time_attrs=(
            "strftime",
            "localtime",
            "gmtime",
            "time_ns",
            "monotonic_ns",
            "process_time",
        ),
    ),
    ConfinedImport(
        names=("asyncio", "select", "selectors"),
        attrs=("os.pidfd_open",),
        allow_paths=("agent_runner/_procwait.py", "agent_runner/_notify.py"),
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
