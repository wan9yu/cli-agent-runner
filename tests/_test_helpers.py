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

# A prompt that passes the startup smoke check (>= 500 bytes); a 1-byte stub
# would fail prompt_smoke_passes and (post-0.1.42) stop serve via config_broken.
_VALID_PROMPT = "placeholder agent task prompt line. " * 20


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
    prompt_file.write_text(_VALID_PROMPT)
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
    vcs_block: str = "",
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
    prompt_file.write_text(_VALID_PROMPT)
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
        f"{prompt_block}\n" + phases_block + vcs_block
    )
    return toml


def read_events_for_current_month(log_dir: Path) -> list[dict]:
    """Read all events from the current month's events-YYYY-MM.jsonl."""
    import json
    from datetime import UTC, datetime

    month = datetime.now(UTC).strftime("%Y-%m")
    events_path = log_dir / f"events-{month}.jsonl"
    return [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]


def make_hook_context(
    tmp_path: Path,
    *,
    agent_name: str = "claude",
    agent_binary: str | None = None,
    round_num: int = 1,
    phase: str | None = None,
    agent_log_path: Path | None = None,
    dry_run: bool = False,
    anomaly_repetitive_window: int = 0,
    anomaly_repetitive_threshold: int = 0,
):
    """Build a minimal HookContext for plugin testing.

    agent_log_path defaults to ``tmp_path/rounds/R{round_num}-test.log`` to
    match where runner.py writes the agent JSONL at runtime. Pair with
    ``write_round_log`` which writes to the same path.

    agent_binary: if not specified, defaults to agent_name so existing tests
    that only pass agent_name continue to exercise plugin guards correctly
    (e.g. agent_name="claude" also seeds agent_binary="claude").
    """
    from agent_runner.hooks import HookContext

    if agent_log_path is None:
        rounds_dir = tmp_path / "rounds"
        rounds_dir.mkdir(exist_ok=True)
        agent_log_path = rounds_dir / f"R{round_num}-test.log"
    return HookContext(
        work_dir=tmp_path,
        log_dir=tmp_path,
        project="testproj",
        round_num=round_num,
        phase=phase,
        agent_name=agent_name,
        agent_binary=agent_binary if agent_binary is not None else agent_name,
        agent_log_path=agent_log_path,
        dry_run=dry_run,
        anomaly_repetitive_window=anomaly_repetitive_window,
        anomaly_repetitive_threshold=anomaly_repetitive_threshold,
    )


def write_round_log(log_dir: Path, round_num: int, events: list[dict]) -> Path:
    """Write fake JSONL to the path where plugins read agent stdout (post-0.1.25)."""
    import json

    rounds_dir = log_dir / "rounds"
    rounds_dir.mkdir(exist_ok=True)
    log_path = rounds_dir / f"R{round_num}-test.log"
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return log_path


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
