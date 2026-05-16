"""Shared test helpers.

Centralises the snapshot+clear+restore fixture pattern used by every test
file that interacts with a plugin-extension registry (hooks, detectors,
event kinds, owned paths). Before: 8 near-identical autouse fixtures across
the test suite. After: one factory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


class FakeArgs:
    """Test stub mimicking argparse.Namespace for serve/monitor CLI tests."""

    def __init__(
        self,
        config: Path,
        *,
        once: bool = True,
        max_rounds: int | None = None,
        port: int = 8765,
        mode: str = "anomaly",
        host: str | None = None,
    ):
        self.config = config
        self.once = once
        self.max_rounds = max_rounds
        self.port = port
        self.mode = mode
        self.host = host


def make_toml(tmp_path: Path) -> Path:
    """Write a minimal agent-runner.toml and return its path.

    Creates ``tmp_path/logs/`` and ``tmp_path/prompt.md`` as side-effects.
    Used by test scaffolding to spin up a valid Config without each test
    repeating the boilerplate.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")
    toml = tmp_path / "agent-runner.toml"
    toml.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n'
        "[prompt]\n"
        f'file = "{prompt_file}"\n'
    )
    return toml


def make_toml_with_sections(
    tmp_path: Path,
    *,
    prompt_block: str | None = None,
    runtime_extra: str = "",
    phases_block: str = "",
) -> Path:
    """Like make_toml but with customizable sections.

    - prompt_block: replaces the default ``file = "<prompt.md>"`` line.
      e.g. ``'files = ["a.md"]'``
    - runtime_extra: additional keys appended inside the ``[runtime]`` section.
      e.g. ``'round_timeout_s = 1800\\n'``
    - phases_block: appended after the ``[prompt]`` section.
      e.g. ``'[phases]\\nlist = ["dev"]\\n'``

    Creates ``tmp_path/logs/`` and ``tmp_path/prompt.md`` as side-effects so
    callers that need a prompt.md on disk (single-file fallback tests) get one
    automatically; tests using prompt_block with different files can ignore it.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("p")
    toml = tmp_path / "agent-runner.toml"
    if prompt_block is None:
        prompt_block = f'file = "{prompt_file}"'
    toml.write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{log_dir}"\n' + runtime_extra + "[prompt]\n"
        f"{prompt_block}\n" + phases_block
    )
    return toml


def read_events_for_current_month(log_dir: Path) -> list[dict]:
    """Read all events from the current month's events-YYYY-MM.jsonl."""
    import json
    from datetime import UTC, datetime

    month = datetime.now(UTC).strftime("%Y-%m")
    events_path = log_dir / f"events-{month}.jsonl"
    return [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]


def isolating(*registries: list[Any] | dict[Any, Any]) -> Any:
    """Return an autouse fixture that snapshots, clears, and restores registries.

    Usage in a test module:

        from tests._test_helpers import isolating
        from agent_runner import monitor

        _reset = isolating(monitor._PLUGIN_DETECTORS)

    Multiple registries can be passed; all are isolated around each test.
    Supports list and dict registries.
    """

    @pytest.fixture(autouse=True)
    def _reset():
        saved: list[Any] = []
        for reg in registries:
            saved.append(reg.copy() if isinstance(reg, dict) else list(reg))
            reg.clear()
        yield
        for reg, snap in zip(registries, saved, strict=True):
            reg.clear()
            if isinstance(reg, dict):
                reg.update(snap)
            else:
                reg.extend(snap)

    return _reset
