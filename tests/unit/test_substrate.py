"""Unit tests for agent_runner._substrate helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch


def test_given_non_git_directory_when_compute_git_head_then_returns_none(tmp_path: Path):
    from agent_runner._substrate import compute_git_head

    result = compute_git_head(tmp_path)
    assert result is None


def test_given_git_directory_when_compute_git_head_then_returns_sha(tmp_path: Path):
    from agent_runner._substrate import compute_git_head

    # Init a fresh git repo with one commit
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-m",
            "init",
            "-q",
        ],
        cwd=tmp_path,
        check=True,
    )
    result = compute_git_head(tmp_path)
    assert result is not None
    assert len(result) >= 7  # at least short SHA
    assert all(c in "0123456789abcdef" for c in result)


def test_given_git_binary_missing_when_compute_git_head_then_returns_none(tmp_path: Path):
    from agent_runner._substrate import compute_git_head

    with patch("agent_runner._substrate.subprocess.run", side_effect=FileNotFoundError):
        result = compute_git_head(tmp_path)
    assert result is None


def test_given_empty_patterns_when_compute_paths_hash_then_returns_none(tmp_path: Path):
    from agent_runner._substrate import compute_paths_hash

    result = compute_paths_hash(tmp_path, [])
    assert result is None


def test_given_same_files_then_hash_is_stable(tmp_path: Path):
    from agent_runner._substrate import compute_paths_hash

    (tmp_path / "a.py").write_text("print('a')\n")
    (tmp_path / "b.py").write_text("print('b')\n")
    h1 = compute_paths_hash(tmp_path, ["*.py"])
    h2 = compute_paths_hash(tmp_path, ["*.py"])
    assert h1 == h2
    assert h1 is not None
    # Now modify b.py → hash changes
    (tmp_path / "b.py").write_text("print('B modified')\n")
    h3 = compute_paths_hash(tmp_path, ["*.py"])
    assert h3 != h1
