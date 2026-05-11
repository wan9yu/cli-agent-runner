"""Shared test fixtures."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a real git repo in tmp_path (commits enabled)."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def tmp_log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def fake_agent_script(tmp_path: Path) -> Path:
    """A 5-line bash script controlled by FAKE_AGENT_BEHAVIOR env var."""
    script = tmp_path / "fake-agent.sh"
    script.write_text(
        '#!/usr/bin/env bash\n'
        'echo "fake agent starting" >&2\n'
        'case "${FAKE_AGENT_BEHAVIOR:-succeed}" in\n'
        '  succeed) exit 0 ;;\n'
        '  dirty)   echo x > "$WORK_DIR/dirty.txt"; exit 0 ;;\n'
        '  hang)    sleep 9999 ;;\n'
        '  crash)   exit 137 ;;\n'
        'esac\n'
    )
    script.chmod(0o755)
    return script


@pytest.fixture
def e2e_pi_enabled() -> bool:
    return bool(os.getenv("AGENT_RUNNER_E2E_PI"))


@pytest.fixture
def in_repo_root() -> Iterator[Path]:
    """For invariant tests that must scan the real codebase."""
    yield Path(__file__).resolve().parent.parent
