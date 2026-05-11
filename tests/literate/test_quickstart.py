"""Execute docs/quickstart.md as a single end-to-end test.

Each ```bash``` block runs in sequence in a shared per-test ``tmp_path``.
Markers (parsed by tests.literate.parser) declare expected substring matches,
exit codes, skips, and per-block env injections.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.literate.parser import parse_literate_blocks

QUICKSTART = Path(__file__).resolve().parent.parent.parent / "docs" / "quickstart.md"


def test_given_quickstart_when_each_bash_block_run_then_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not QUICKSTART.exists():
        pytest.skip("docs/quickstart.md not yet present")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Use the currently-active interpreter's bin dir — works whether the test
    # runner is .venv, .tox, uv, or a system Python.
    interp_bin = Path(sys.executable).parent
    monkeypatch.setenv("PATH", f"{interp_bin}:{os.environ['PATH']}")

    blocks = parse_literate_blocks(QUICKSTART.read_text(encoding="utf-8"))
    assert blocks, "quickstart.md has no bash blocks — literate runner is a no-op"

    for i, block in enumerate(blocks):
        if block.skip:
            continue
        env = {**os.environ, **block.env}
        r = subprocess.run(
            ["bash", "-c", block.code],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert r.returncode == block.expected_status, (
            f"block #{i} at quickstart.md:{block.line} expected exit "
            f"{block.expected_status}, got {r.returncode}\n"
            f"command: {block.code!r}\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
        if block.expected_substring is not None:
            assert block.expected_substring in r.stdout, (
                f"block #{i} at quickstart.md:{block.line} stdout missing "
                f"substring {block.expected_substring!r}\nstdout: {r.stdout}"
            )
