from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_runner import __version__
from agent_runner.cli import main


def _write_minimal_toml(tmp_git_repo: Path, fake_agent: Path) -> Path:
    toml = tmp_git_repo / "agent-runner.toml"
    prompt = tmp_git_repo / "p.md"
    prompt.write_text("Body. " * 200)
    log_dir = tmp_git_repo / "logs"
    toml.write_text(f"""
[agent]
command = ["{fake_agent}"]
prompt_arg_template = ["{{prompt}}"]
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


def test_main_round_should_write_status_file_when_invoked(
    tmp_git_repo: Path,
    fake_agent_script: Path,
) -> None:
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)

    rc = main(["--config", str(toml), "round"])

    assert rc == 0
    status = json.loads((tmp_git_repo / "logs" / "status.json").read_text())
    assert status["round_num"] == 1


def test_main_round_should_find_toml_when_config_flag_used_from_external_cwd(
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


def test_main_status_should_exit_cleanly_when_invoked(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    capsys,
) -> None:
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)

    rc = main(["--config", str(toml), "status"])

    assert rc == 0


def test_main_status_should_print_round_or_mode_info_when_invoked_after_one_round(
    tmp_git_repo: Path,
    fake_agent_script: Path,
    capsys,
) -> None:
    toml = _write_minimal_toml(tmp_git_repo, fake_agent_script)

    main(["--config", str(toml), "round"])
    main(["--config", str(toml), "status"])

    out = capsys.readouterr().out.lower()

    assert "mode: none" in out
    assert "active: false" in out


def test_main_should_print_version_and_exit_zero_when_version_flag_given(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--version prints 'agent-runner <version>' and exits 0 via SystemExit."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert f"agent-runner {__version__}" in captured.out


def test_main_round_should_pass_phase_override_to_run_one_round_when_phase_flag_given(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`round --phase NAME` plumbs the override into run_one_round."""
    from agent_runner.api_types import RoundResult

    captured = {}

    def fake_run_one_round(cfg, *, phase_override=None):
        captured["phase_override"] = phase_override
        return RoundResult(
            round_num=1,
            phase=phase_override,
            started_at="2026-01-01T00:00:00.000Z",
            ended_at="2026-01-01T00:00:01.000Z",
            exit_code=0,
            duration_s=0.0,
            timed_out=False,
            log_path=tmp_path / "R1.log",
            dirty_files=[],
            stashed=False,
        )

    monkeypatch.setattr("agent_runner.cli.round_cmd.run_one_round", fake_run_one_round)

    # Write minimal config with phases
    (tmp_path / "prompt.md").write_text("p")
    (tmp_path / "agent-runner.toml").write_text(
        "[agent]\n"
        'command = ["true"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "[runtime]\n"
        f'work_dir = "{tmp_path}"\n'
        f'log_dir = "{tmp_path}/logs"\n'
        "[prompt]\n"
        f'file = "{tmp_path}/prompt.md"\n'
        "[phases]\n"
        'list = ["dev", "qa", "product"]\n'
    )

    rc = main(["--config", str(tmp_path / "agent-runner.toml"), "round", "--phase", "product"])

    assert rc == 0
    assert captured["phase_override"] == "product"
