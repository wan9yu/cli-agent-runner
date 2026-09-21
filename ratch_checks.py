"""ratch gates for facts this repo does not keep in pytest.

BDD, AI-signatures (tags + CHANGELOG), and injected-clock stay in pytest
until ratch dialects match. Whole-repo loc-cap waits on four test files
over 1000 lines.
"""

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
    CatalogSize(),
    CommitHeatmap(),
]
