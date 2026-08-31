"""_primary_prompt_file is a runner-only helper and lives beside runner, not in api."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_primary_prompt_file_lives_in_runner_not_api() -> None:
    from agent_runner import api, runner

    assert hasattr(runner, "_primary_prompt_file")
    assert not hasattr(api, "_primary_prompt_file")


def test_primary_prompt_file_prefers_files_then_falls_back_to_file() -> None:
    from agent_runner.runner import _primary_prompt_file

    cfg = SimpleNamespace(prompt=SimpleNamespace(files=[Path("a.md")], file=Path("b.md")))
    assert _primary_prompt_file(cfg) == Path("a.md")

    cfg_single = SimpleNamespace(prompt=SimpleNamespace(files=[], file=Path("b.md")))
    assert _primary_prompt_file(cfg_single) == Path("b.md")
