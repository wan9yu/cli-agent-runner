"""End-to-end: plugin-owned paths are honored by detect_dirty_files and stash_orphan."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_runner.vcs_state import _PLUGIN_OWNED_PATHS
from tests._test_helpers import isolating

_reset = isolating(_PLUGIN_OWNED_PATHS)


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


def _stash_contents(repo: Path) -> list[str]:
    """Paths captured in stash@{0}, including untracked ones."""
    r = subprocess.run(
        ["git", "stash", "show", "--include-untracked", "--name-only", "stash@{0}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.split()


def test_given_owned_and_non_owned_dirty_when_stash_orphan_then_owned_file_survives(
    tmp_git_repo: Path,
) -> None:
    """The registry must bind at the git boundary, not only the report boundary.

    Deliberately no ``_intent_to_add`` here: intent-to-add entries make
    ``git stash push -u`` fail rc=1, which would green this test for the wrong reason.
    """
    from agent_runner.vcs_state import register_plugin_owned_paths, stash_orphan

    register_plugin_owned_paths(["proposals/"])
    (tmp_git_repo / "proposals").mkdir()
    (tmp_git_repo / "proposals" / "memo.md").write_text("deliverable\n")
    (tmp_git_repo / "src.py").write_text("agent work\n")

    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None)

    assert ref is not None
    assert (tmp_git_repo / "proposals" / "memo.md").read_text() == "deliverable\n"
    assert "proposals/memo.md" not in _stash_contents(tmp_git_repo)
    assert "src.py" in _stash_contents(tmp_git_repo)


def test_given_gitignored_owned_path_when_stash_orphan_then_push_is_not_refused(
    tmp_git_repo: Path,
) -> None:
    """An ignore-matched owned path must stay out of the pathspec.

    0.1.42 lesson: naming an ignore-matched path in a stash pathspec makes git
    return rc=1 on the whole push. Guards the gate, so it holds before and after
    the fix and fails only on an ungated one.
    """
    from agent_runner.vcs_state import register_plugin_owned_paths, stash_orphan

    (tmp_git_repo / ".gitignore").write_text("proposals/\n")
    (tmp_git_repo / "proposals").mkdir()
    (tmp_git_repo / "proposals" / "memo.md").write_text("v1\n")
    subprocess.run(
        ["git", "add", "-f", "proposals/memo.md", ".gitignore"], cwd=tmp_git_repo, check=True
    )
    _commit(tmp_git_repo, "track a file inside an ignored dir")

    register_plugin_owned_paths(["proposals/"])
    (tmp_git_repo / "proposals" / "memo.md").write_text("v2 deliverable\n")
    (tmp_git_repo / "src.py").write_text("agent work\n")

    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None)

    assert ref is not None  # push was not refused
    assert "src.py" in _stash_contents(tmp_git_repo)


def test_given_glob_owned_path_in_fresh_untracked_dir_when_stash_orphan_then_survives(
    tmp_git_repo: Path,
) -> None:
    """A glob-form owned path inside a wholly-untracked dir must survive the stash.

    ``git status --porcelain`` collapses a fresh untracked directory to ``reports/``,
    which ``reports/*.md`` does not match -- so the exclude scan has to look at
    concrete files (``-uall``) or the deliverable is swept exactly as before the fix.
    This is the first-round shape, and the registration the original incident used.
    """
    from agent_runner.vcs_state import register_plugin_owned_paths, stash_orphan

    register_plugin_owned_paths(["reports/*.md"])
    (tmp_git_repo / "reports").mkdir()
    (tmp_git_repo / "reports" / "dev.md").write_text("deliverable\n")
    (tmp_git_repo / "src.py").write_text("agent work\n")

    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None)

    assert ref is not None
    assert (tmp_git_repo / "reports" / "dev.md").read_text() == "deliverable\n"
    assert "reports/dev.md" not in _stash_contents(tmp_git_repo)
    assert "src.py" in _stash_contents(tmp_git_repo)


def test_given_dash_prefixed_ignored_owned_path_when_stash_orphan_then_push_not_refused(
    tmp_git_repo: Path,
) -> None:
    """A leading-dash owned path must not turn the ignore gate into an error.

    Without a ``--`` separator git parses ``-out/memo.md`` as a switch and exits 129.
    ``rc != 0`` reads as "not ignored", so the ignore-matched path enters the pathspec,
    ``git stash push -u`` is refused rc=1, and the orphan defense silently does not run.

    The dash must lead the *porcelain* path for git to misparse it, and the ignored
    entry must be a directory for the refusal to trigger -- hence a tracked file
    inside a dash-prefixed ignored dir.
    """
    from agent_runner.vcs_state import register_plugin_owned_paths, stash_orphan

    (tmp_git_repo / ".gitignore").write_text("-out/\n")
    (tmp_git_repo / "-out").mkdir()
    (tmp_git_repo / "-out" / "memo.md").write_text("v1\n")
    subprocess.run(
        ["git", "add", "-f", "--", "-out/memo.md", ".gitignore"], cwd=tmp_git_repo, check=True
    )
    _commit(tmp_git_repo, "track a file inside a dash-prefixed ignored dir")

    register_plugin_owned_paths(["-out/"])
    (tmp_git_repo / "-out" / "memo.md").write_text("v2 deliverable\n")
    (tmp_git_repo / "src.py").write_text("agent work\n")

    ref = stash_orphan(tmp_git_repo, round_num=1, phase=None)

    assert ref is not None  # push was not refused
    assert "src.py" in _stash_contents(tmp_git_repo)
