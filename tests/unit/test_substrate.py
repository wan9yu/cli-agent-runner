"""Unit tests for agent_runner._substrate helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_compute_git_head_should_return_none_when_directory_not_git(tmp_path: Path):
    from agent_runner._substrate import compute_git_head

    result = compute_git_head(tmp_path)

    assert result is None


def test_compute_git_head_should_return_sha_when_directory_is_git(tmp_git_repo: Path):
    from agent_runner._substrate import compute_git_head

    result = compute_git_head(tmp_git_repo)

    assert result is not None
    assert len(result) >= 7  # at least short SHA
    assert all(c in "0123456789abcdef" for c in result)


def test_compute_git_head_should_return_none_when_git_binary_missing(tmp_path: Path):
    from agent_runner._substrate import compute_git_head

    with patch("agent_runner._substrate.subprocess.run", side_effect=FileNotFoundError):
        result = compute_git_head(tmp_path)

    assert result is None


def test_compute_paths_hash_should_return_none_when_patterns_empty(tmp_path: Path):
    from agent_runner._substrate import compute_paths_hash

    result = compute_paths_hash(tmp_path, [])

    assert result is None


def test_compute_paths_hash_should_be_stable_when_files_unchanged(tmp_path: Path):
    from agent_runner._substrate import compute_paths_hash

    (tmp_path / "a.py").write_text("print('a')\n")
    (tmp_path / "b.py").write_text("print('b')\n")

    h1 = compute_paths_hash(tmp_path, ["*.py"])
    h2 = compute_paths_hash(tmp_path, ["*.py"])

    assert h1 == h2
    assert h1 is not None


def test_compute_paths_hash_should_change_when_file_content_changes(tmp_path: Path):
    from agent_runner._substrate import compute_paths_hash

    (tmp_path / "a.py").write_text("print('a')\n")
    (tmp_path / "b.py").write_text("print('b')\n")
    h1 = compute_paths_hash(tmp_path, ["*.py"])

    (tmp_path / "b.py").write_text("print('B modified')\n")
    h3 = compute_paths_hash(tmp_path, ["*.py"])

    assert h3 != h1
