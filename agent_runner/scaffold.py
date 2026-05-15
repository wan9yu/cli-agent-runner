"""Project scaffold for `agent-runner init`.

Writes three files into a git repo:
  agent-runner.toml      — copy of selected preset, project name substituted
  prompts/main.md        — neutral 8-line placeholder
  .gitignore             — append "logs/" if missing

Available presets ship as package data in `agent_runner/presets/*.toml`.
Currently: `claude`, `aider`, `gemini`.

Optionally commits in one step (default true via the CLI).
"""

from __future__ import annotations

import importlib.resources
import subprocess  # noqa: TID251 — scaffold needs git for the commit step
from pathlib import Path

from agent_runner.api_types import InitResult
from agent_runner.vcs_state import is_git_repo


def _load_preset(name: str) -> str:
    """Read preset TOML text; raises FileNotFoundError if name unknown."""
    presets = importlib.resources.files("agent_runner.presets")
    return (presets / f"{name}.toml").read_text(encoding="utf-8")


_PROMPT_TEMPLATE = """\
# Agent Prompt

You are an autonomous agent working on this project. Each round begins with a
`round-context` JSON block prepended above this prompt — read it first.

If `round_num == 1`: orient yourself with the project structure (README, file tree).
If `previous.exit_code != 0`: investigate what went wrong before resuming.
If `orphan_stash` is present: decide salvage (`git stash pop`) or abandon (`git stash drop`).

Always: commit and push your work before exiting the round. The supervisor will
auto-stash if you forget, but explicit commits with meaningful messages are better.
"""

_GITIGNORE_LINE = "logs/"


def scaffold_project(
    work_dir: Path,
    *,
    preset: str = "claude",
    force: bool,
    commit: bool,
) -> InitResult:
    if not is_git_repo(work_dir):
        raise RuntimeError(f"{work_dir} is not a git working tree — run `git init` first")

    toml_path = work_dir / "agent-runner.toml"
    prompt_dir = work_dir / "prompts"
    prompt_path = prompt_dir / "main.md"
    gitignore_path = work_dir / ".gitignore"

    if toml_path.exists() and not force:
        raise FileExistsError(f"{toml_path} already exists; pass force=True to overwrite")

    files_created: list[Path] = []

    project = work_dir.resolve().name or "default"
    toml_text = _load_preset(preset).replace("{project}", project)
    toml_path.write_text(toml_text)
    files_created.append(toml_path)

    prompt_dir.mkdir(parents=True, exist_ok=True)
    if not prompt_path.exists() or force:
        prompt_path.write_text(_PROMPT_TEMPLATE)
        files_created.append(prompt_path)

    existing = gitignore_path.read_text() if gitignore_path.exists() else ""
    if _GITIGNORE_LINE not in existing.splitlines():
        new_text = existing
        if existing and not existing.endswith("\n"):
            new_text += "\n"
        new_text += _GITIGNORE_LINE + "\n"
        gitignore_path.write_text(new_text)
        files_created.append(gitignore_path)

    committed = False
    if commit:
        subprocess.run(["git", "add", "."], cwd=work_dir, check=True)
        r = subprocess.run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-q",
                "-m",
                "chore: agent-runner initial config",
            ],
            cwd=work_dir,
            capture_output=True,
            text=True,
        )
        committed = r.returncode == 0  # may fail if nothing changed

    return InitResult(
        work_dir=work_dir,
        files_created=files_created,
        committed=committed,
        preset=preset,
    )
