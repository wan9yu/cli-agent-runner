"""Invariant: `agent-runner peek --json` always emits a top-level
`schema_version` (>= "1.0") and `plugins` namespace.

Plugin observers (Argus Gateway, monitoring tooling) parse this. Forgetting
to emit the wrapper silently breaks them — this test fails loud instead.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_given_peek_json_when_emitted_then_includes_schema_version(tmp_path: Path) -> None:
    work_dir = tmp_path / "proj"
    work_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work_dir, check=True)
    (work_dir / "README.md").write_text("scaffold\n")
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "add", "."],
        cwd=work_dir,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.email=t@t.t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        cwd=work_dir,
        check=True,
    )

    bin_dir = Path(sys.executable).parent
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}

    subprocess.run(
        ["agent-runner", "init", "--no-commit"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        ["agent-runner", "peek", "--json"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert "schema_version" in payload, f"missing schema_version: keys={list(payload)}"
    assert payload["schema_version"] >= "1.8", (
        f"schema_version regressed: got {payload['schema_version']!r}, expected >= '1.8'"
    )
    assert "plugins" in payload
    assert isinstance(payload["plugins"], dict)
    assert "event_kinds" in payload["plugins"]
    assert isinstance(payload["plugins"]["event_kinds"], list)
    assert "context_enrichers" in payload["plugins"]
    assert isinstance(payload["plugins"]["context_enrichers"], list)
    assert "detectors" in payload["plugins"], (
        f"plugins namespace missing detectors key: {payload['plugins']}"
    )
    assert isinstance(payload["plugins"]["detectors"], list)
