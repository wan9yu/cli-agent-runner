"""End-to-end: plugin-owned paths are filtered out of detect_dirty_files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _commit(repo: Path, msg: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            msg,
        ],
        cwd=repo,
        check=True,
    )


@pytest.fixture(autouse=True)
def _clear_registry():
    """Each test starts with a clean plugin-owned-paths registry."""
    from agent_runner.vcs_state import _PLUGIN_OWNED_PATHS

    saved = list(_PLUGIN_OWNED_PATHS)
    _PLUGIN_OWNED_PATHS.clear()
    yield
    _PLUGIN_OWNED_PATHS.clear()
    _PLUGIN_OWNED_PATHS.extend(saved)


def _intent_to_add(repo: Path) -> None:
    """Mark all untracked files with ``git add -N`` so ``status --porcelain``
    lists them individually (rather than collapsing into the parent dir).

    Real-world plugins write files into already-tracked directories, so
    per-file porcelain entries are the production reality. This helper
    reproduces that shape from a fresh tmp_git_repo.
    """
    subprocess.run(["git", "add", "-N", "."], cwd=repo, check=True)


def test_given_no_registration_when_dirty_files_then_all_returned(
    tmp_git_repo: Path,
) -> None:
    """Baseline: with no registration, today's behavior is preserved."""
    from agent_runner.vcs_state import detect_dirty_files

    (tmp_git_repo / "proposals").mkdir()
    (tmp_git_repo / "proposals" / "report.md").write_text("hello\n")
    (tmp_git_repo / "other.txt").write_text("x\n")
    _intent_to_add(tmp_git_repo)

    dirty = detect_dirty_files(tmp_git_repo)
    assert "other.txt" in dirty
    assert "proposals/report.md" in dirty


def test_given_proposals_registered_when_dirty_files_then_proposals_filtered(
    tmp_git_repo: Path,
) -> None:
    """Plugin-owned 'proposals/' files are excluded from dirty list."""
    from agent_runner.vcs_state import detect_dirty_files, register_plugin_owned_paths

    register_plugin_owned_paths(["proposals/"])
    (tmp_git_repo / "proposals").mkdir()
    (tmp_git_repo / "proposals" / "report.md").write_text("hello\n")
    (tmp_git_repo / "other.txt").write_text("x\n")
    _intent_to_add(tmp_git_repo)

    dirty = detect_dirty_files(tmp_git_repo)
    assert "other.txt" in dirty
    assert "proposals/report.md" not in dirty


def test_given_recursive_glob_registered_when_dirty_files_then_deep_paths_filtered(
    tmp_git_repo: Path,
) -> None:
    """Recursive ``**`` glob excludes nested paths too."""
    from agent_runner.vcs_state import detect_dirty_files, register_plugin_owned_paths

    register_plugin_owned_paths(["logs/plugins/**/*"])
    (tmp_git_repo / "logs" / "plugins" / "argus").mkdir(parents=True)
    (tmp_git_repo / "logs" / "plugins" / "argus" / "state.json").write_text("{}\n")
    (tmp_git_repo / "logs" / "other.log").write_text("x\n")
    _intent_to_add(tmp_git_repo)

    dirty = detect_dirty_files(tmp_git_repo)
    assert "logs/plugins/argus/state.json" not in dirty
    assert "logs/other.log" in dirty
