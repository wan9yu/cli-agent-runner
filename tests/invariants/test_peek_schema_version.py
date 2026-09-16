"""Invariant: `agent-runner peek --json` always emits a top-level
`schema_version` (>= "1.0") and `plugins` namespace.

Plugin observers (a downstream integrator, monitoring tooling) parse this. Forgetting
to emit the wrapper silently breaks them — this test fails loud instead.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent_runner.cli.common import PEEK_SCHEMA_VERSION


def _ver(s: str) -> tuple[int, ...]:
    """Parse a dotted version to an int tuple — string ``>=`` mis-orders "1.10" < "1.9"."""
    return tuple(int(x) for x in s.split("."))


def test_peek_json_should_include_schema_version_when_emitted(tmp_path: Path) -> None:
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
    assert _ver(payload["schema_version"]) >= _ver(PEEK_SCHEMA_VERSION), (
        f"schema_version regressed: got {payload['schema_version']!r}, "
        f"expected >= {PEEK_SCHEMA_VERSION!r}"
    )
    assert payload["schema_version"] == "2.5"
    assert "brake" in payload
    assert payload["brake"] in ("off", "armed", "inert(undelegated)", "inert(no cgroup v2)")
    assert "plugins" in payload
    assert isinstance(payload["plugins"], dict)
    assert "post_round_hooks" in payload["plugins"]
    assert isinstance(payload["plugins"]["post_round_hooks"], list)
    assert "sandbox" in payload["plugins"], (
        f"plugins namespace missing sandbox key: {payload['plugins']}"
    )
    assert isinstance(payload["plugins"]["sandbox"], dict)
    assert "covers" in payload["plugins"]["sandbox"]
    assert "pins" in payload["plugins"], f"plugins namespace missing pins key: {payload['plugins']}"
    assert isinstance(payload["plugins"]["pins"], dict)
    assert "spawn_override_allow" in payload["plugins"], (
        f"plugins namespace missing spawn_override_allow key: {payload['plugins']}"
    )
    assert isinstance(payload["plugins"]["spawn_override_allow"], list)
