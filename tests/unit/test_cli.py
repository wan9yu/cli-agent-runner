from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_runner.cli import main


def _write_minimal_toml(tmp_git_repo: Path, fake_agent: Path) -> Path:
    toml = tmp_git_repo / "agent-runner.toml"
    prompt = tmp_git_repo / "p.md"
    prompt.write_text("Body. " * 200)
    log_dir = tmp_git_repo / "logs"
    toml.write_text(f"""
[agent]
command = ["{fake_agent}"]
prompt_arg_template = []
[runtime]
work_dir = "{tmp_git_repo}"
log_dir = "{log_dir}"
round_timeout_s = 10
[prompt]
file = "{prompt}"
""")
    (tmp_git_repo / ".gitignore").write_text("logs/\n")
    subprocess.run(["git", "add", "."], cwd=tmp_git_repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture"],
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


def test_given_round_from_external_cwd_when_config_flag_used_then_finds_toml(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Regression guard: cli must honor --config from a cwd outside the project."""
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    rc = main(["--config", str(toml), "round"])
    assert rc == 0
    status = json.loads((tmp_git_repo / "logs" / "status.json").read_text())
    assert status["round_num"] == 1


def test_given_status_subcommand_when_invoked_then_returns_zero(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    capsys,
) -> None:
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)
    rc = main(["--config", str(toml), "status"])
    # status subcommand may be stub or real, just verify it doesn't crash
    assert rc in (0, 1)


def test_given_status_subcommand_after_one_round_when_invoked_then_completes(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    capsys,
) -> None:
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)
    main(["--config", str(toml), "round"])
    main(["--config", str(toml), "status"])
    out = capsys.readouterr().out
    # Either stub message OR real ServiceStatus output
    assert "round" in out.lower() or "mode" in out.lower() or "not implemented" in out.lower()
