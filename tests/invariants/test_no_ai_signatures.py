"""Invariant: no AI-tool attribution anywhere in the curated git history.

Rationale: this project's commit log is curated to credit the human author
only. AI-tool trailers (``Co-Authored-By: Claude``, ``🤖 Generated with``,
``noreply@anthropic.com``, etc.) cause synthetic "claude" entries in GitHub's
Contributors widget and pollute the commit log.

Three sources are scanned:
  1. Every commit message body in ``HEAD``'s history
  2. Every annotated tag's annotation body
  3. ``CHANGELOG.md`` content

A pre-commit hook (``.githooks/commit-msg``) and a CI lint job
(``.github/workflows/ci.yml``: ``lint-commits``) provide upstream defense;
this invariant is the rear guard.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

# Patterns that constitute AI-tool attribution. Bare mentions of "claude" or
# "anthropic" in commit subjects are NOT forbidden (the project name CLI itself
# may legitimately reference them in error messages, runbook prose, etc.) —
# only the specific signature trailers + Generated-with prefix + noreply email.
_FORBIDDEN = re.compile(
    r"(?:Co-Authored-By:|🤖|Generated with Claude|Generated with \[Cursor\]"
    r"|noreply@anthropic\.com)",
    re.IGNORECASE,
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def test_given_main_history_when_scanned_then_no_ai_attribution_in_commit_messages() -> None:
    """Every commit's message body must be free of AI-tool attribution."""
    log = _git("log", "--format=%H%x1f%B%x1e", "HEAD")
    offenders: list[tuple[str, str]] = []
    for entry in log.split("\x1e"):
        entry = entry.strip()
        if not entry:
            continue
        sha, _, body = entry.partition("\x1f")
        m = _FORBIDDEN.search(body)
        if m:
            offenders.append((sha[:8], m.group(0)))
    assert not offenders, (
        f"AI-tool attribution found in commit messages: {offenders}. "
        f"Run `git log --format=%B` and grep for the matched pattern."
    )


def test_given_tag_annotations_when_scanned_then_no_ai_attribution() -> None:
    """Every annotated tag's annotation body must be free of AI-tool attribution."""
    tags = _git("tag", "--list").splitlines()
    offenders: list[tuple[str, str]] = []
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        body = _git("for-each-ref", f"refs/tags/{tag}", "--format=%(contents)")
        m = _FORBIDDEN.search(body)
        if m:
            offenders.append((tag, m.group(0)))
    assert not offenders, (
        f"AI-tool attribution found in tag annotations: {offenders}. "
        f"Re-create the tag with a clean message."
    )


def test_given_changelog_content_when_scanned_then_no_ai_attribution() -> None:
    """CHANGELOG.md must not advertise AI-tool attribution."""
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    m = _FORBIDDEN.search(changelog)
    assert m is None, f"AI-tool attribution found in CHANGELOG.md: matched {m.group(0)!r}"
