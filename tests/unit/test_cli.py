from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_runner.cli import main


def _write_minimal_toml(tmp_git_repo: Path, fake_agent: Path) -> Path:
    """Write toml + prompt inside tmp_git_repo and commit them.

    The toml/prompt live inside the work_dir for production realism (a real
    project keeps its own runner config tracked alongside the code). We commit
    them so the agent's clean-exit-with-dirty-tree stash logic does not sweep
    them away between successive `main()` invocations within one test.
    """
    toml = tmp_git_repo / "agent-runner.toml"
    prompt = tmp_git_repo / "p.md"
    prompt.write_text("Body. " * 200)
    log_dir = tmp_git_repo / "logs"
    toml.write_text(
        f"""
[agent]
command = ["{fake_agent}"]
prompt_arg_template = []
[runtime]
work_dir = "{tmp_git_repo}"
log_dir = "{log_dir}"
round_timeout_s = 10
[prompt]
file = "{prompt}"
"""
    )
    # Make sure logs/ never lands in the tracked tree (it is a runtime artifact).
    gitignore = tmp_git_repo / ".gitignore"
    gitignore.write_text("logs/\n")
    subprocess.run(
        ["git", "add", "agent-runner.toml", "p.md", ".gitignore"],
        cwd=tmp_git_repo,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "add runner config"],
        cwd=tmp_git_repo,
        check=True,
    )
    return toml


def test_given_round_command_when_invoked_then_status_file_written(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)
    rc = main(["--config", str(toml), "round"])
    assert rc == 0
    status = json.loads((tmp_git_repo / "logs" / "status.json").read_text())
    assert status["round_num"] == 1


def test_given_status_flag_when_invoked_with_no_status_file_then_prints_no_status(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    capsys,
) -> None:
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)
    rc = main(["--config", str(toml), "--status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no status" in out.lower() or "round_num" in out.lower()


def test_given_status_flag_after_one_round_when_invoked_then_prints_round_num(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    capsys,
) -> None:
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)
    main(["--config", str(toml), "round"])
    main(["--config", str(toml), "--status"])
    out = capsys.readouterr().out
    assert "round_num" in out
    assert "1" in out
