"""First ratch cut: hygiene we do not already gate, plus two eyes.

Not a second pytest. Do not enable loc-cap / skip / bdd / injected-clock /
forbidden-literal / font-cdn until those dialects match this repo.
"""

from ratch.checks.catalog_size import CatalogSize
from ratch.checks.commit_heatmap import CommitHeatmap
from ratch.checks.conflict_markers import NoConflictMarkers
from ratch.checks.hash_named_test import NoHashNamedTest

CHECKS = [
    NoConflictMarkers(),
    NoHashNamedTest(),
    CatalogSize(),  # eye: print only
    CommitHeatmap(),  # eye: directory x file x time
]
