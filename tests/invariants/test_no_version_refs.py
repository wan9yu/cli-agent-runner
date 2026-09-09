"""Invariant: no historical release-version / SDD-task breadcrumbs in agent_runner/.

Production code used to carry ad hoc "0.2.17 Task 1" / "pre-0.2.18" / "Group C
seam 3" annotations marking WHICH release or planning task added a piece of
code. None of them ever drove any logic (no version comparisons or branches
existed anywhere), and none of them helped a reader understand present
behavior once the release had shipped. Scrubbed once, this test keeps them
scrubbed.

A handful of genuinely FUNCTIONAL version numbers remain and are allowlisted
below by file — they are user-facing output, not code-provenance breadcrumbs:

- ``migrations.py``: the ``year=`` migration-target-version parameters (and
  the literal ``describe``/``raise`` text alongside them) name the exact
  config-format version a key was renamed/rejected in — that IS the point of
  ``agent-runner migrate``'s output, not a comment about when code was added.
- ``config/parsers.py``: ``ConfigError`` messages naming the version a
  removed setting was removed in, for the identical reason.
- ``cli/upgrade_cmd.py``: ``--target``'s help/error text uses an illustrative
  PyPI version string (``e.g. 0.1.13``) — a format example, not a
  code-provenance breadcrumb.
- ``defenses.py``: ``Defense.codifies`` incident narratives are re-exported
  live via ``api.peek`` and the generated ``docs/architecture.md`` defenses
  table — functional output, not a comment.
- ``__init__.py``: the ``ImportError`` fallback ``__version__`` constant.
- ``_version.py``: hatch-vcs generated at build time.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# A version-shaped number NOT preceded by another digit/dot -- excludes false
# positives inside a literal dotted-quad IPv4 address (e.g. "127.0.0.1"
# contains "0.0.1", which would otherwise match this pattern).
_VERSION_REF = re.compile(r"(?<![\d.])0\.[0-9]+\.[0-9]+\b|(?<![\d.])pre-0\.[0-9]")

_ALLOWLIST_FILES = {
    "agent_runner/_version.py",
    "agent_runner/__init__.py",
    "agent_runner/migrations.py",
    "agent_runner/config/parsers.py",
    "agent_runner/cli/upgrade_cmd.py",
    "agent_runner/defenses.py",
}


def _agent_runner_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "agent_runner"], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout
    return [p for p in out.splitlines() if p.endswith(".py") and p not in _ALLOWLIST_FILES]


def _scan(paths: list[Path]) -> list[str]:
    hits = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _VERSION_REF.search(line):
                hits.append(f"{path}:{i}: {line.strip()[:88]}")
    return hits


def test_given_agent_runner_files_when_scanned_then_no_version_refs() -> None:
    rel_paths = _agent_runner_files()
    # vacuity guard
    assert len(rel_paths) > 50, "agent_runner/ scan found too few files — scan is vacuous"
    hits = _scan([_REPO / p for p in rel_paths])
    assert not hits, (
        "release-version / SDD-task breadcrumbs leaked into agent_runner/:\n" + "\n".join(hits)
    )


def test_given_a_reintroduced_version_breadcrumb_when_scanned_then_flagged(tmp_path: Path) -> None:
    """Non-vacuity proof: the scanner fires on the exact shape this cleanup
    removed, so a regression re-adding one would fail CI, not slip by."""
    offender = tmp_path / "some_module.py"
    offender.write_text('"""Exit-0 no-progress breaker (0.2.16 Task 6). Some CLIs exit 0..."""\n')
    assert _scan([offender]), "scanner failed to flag a reintroduced 0.2.X breadcrumb"

    pre_offender = tmp_path / "some_other_module.py"
    pre_offender.write_text('"""Unchanged pre-0.2.17 behavior."""\n')
    assert _scan([pre_offender]), "scanner failed to flag a reintroduced pre-0.2.X breadcrumb"

    clean = tmp_path / "clean_module.py"
    clean.write_text('"""Exit-0 no-progress breaker. Some CLIs exit 0..."""\n')
    assert not _scan([clean]), "scanner false-positived on breadcrumb-free text"


def test_given_an_ipv4_literal_when_scanned_then_not_flagged(tmp_path: Path) -> None:
    """The dotted-quad guard: an IPv4 literal like 127.0.0.1 (it contains
    "0.0.1") must not be mistaken for a release version."""
    ip_only = tmp_path / "ip_module.py"
    ip_only.write_text('SERVER_ADDR = ("127.0.0.1", 0)\n')
    assert not _scan([ip_only]), "scanner false-positived on an IPv4 literal"
