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
