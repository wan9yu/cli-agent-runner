from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner.cli import main


def test_cli_init_should_create_config_files_when_run_in_git_repo(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_git_repo)

    rc = main(["init", "--no-commit"])

    assert rc == 0
    assert (tmp_git_repo / "agent-runner.toml").exists()
    assert (tmp_git_repo / "prompts" / "main.md").exists()


def test_cli_install_should_call_api_install_when_invoked(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    main(["init", "--no-commit"])

    with patch("agent_runner.api.install") as install:
        install.return_value = type(
            "R", (), {"unit_path": tmp_git_repo / "u.service", "monitor_unit_path": None}
        )()
        rc = main(["install"])
        assert rc == 0
        install.assert_called_once()


def test_cli_install_should_pass_with_monitor_true_when_monitor_flag_given(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    main(["init", "--no-commit"])

    with patch("agent_runner.api.install") as install:
        install.return_value = type(
            "R",
            (),
            {
                "unit_path": tmp_git_repo / "u.service",
                "monitor_unit_path": tmp_git_repo / "m.service",
            },
        )()
        main(["install", "--monitor"])
        kwargs = install.call_args.kwargs
        assert kwargs["with_monitor"] is True


def test_cli_uninstall_should_call_api_uninstall_when_invoked(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    main(["init", "--no-commit"])

    with patch("agent_runner.api.uninstall", return_value=True) as un:
        rc = main(["uninstall"])
        assert rc == 0
        un.assert_called_once()


def test_cli_init_should_propagate_preset_flag_to_api_when_preset_given(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    monkeypatch.chdir(tmp_git_repo)

    rc = main(["init", "--preset", "aider", "--no-commit"])

    assert rc == 0
    toml_text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert 'command = ["aider"' in toml_text


def test_cli_init_should_default_to_claude_preset_when_no_preset_flag_given(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    monkeypatch.chdir(tmp_git_repo)

    rc = main(["init", "--no-commit"])

    assert rc == 0
    toml_text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert 'command = ["claude"' in toml_text


def test_cli_init_should_reject_invalid_preset_via_argparse() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["init", "--preset", "nonexistent"])

    assert exc.value.code != 0
