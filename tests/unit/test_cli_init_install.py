from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_runner.cli import main


def test_given_init_subcommand_in_git_repo_when_invoked_then_creates_files(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    rc = main(["init", "--no-commit"])
    assert rc == 0
    assert (tmp_git_repo / "agent-runner.toml").exists()
    assert (tmp_git_repo / "prompts" / "main.md").exists()


def test_given_install_when_invoked_then_calls_api_install(
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


def test_given_install_with_monitor_flag_when_invoked_then_passes_with_monitor_true(
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


def test_given_uninstall_when_invoked_then_calls_api_uninstall(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    main(["init", "--no-commit"])
    with patch("agent_runner.api.uninstall", return_value=True) as un:
        rc = main(["uninstall"])
        assert rc == 0
        un.assert_called_once()


def test_given_init_command_when_preset_flag_passed_then_propagates_to_api(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    """CLI --preset aider arg reaches api.init()."""
    monkeypatch.chdir(tmp_git_repo)
    rc = main(["init", "--preset", "aider", "--no-commit"])
    assert rc == 0
    toml_text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert 'command = ["aider"' in toml_text


def test_given_init_command_when_no_preset_flag_then_defaults_to_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_git_repo: Path
) -> None:
    monkeypatch.chdir(tmp_git_repo)
    rc = main(["init", "--no-commit"])
    assert rc == 0
    toml_text = (tmp_git_repo / "agent-runner.toml").read_text()
    assert 'command = ["claude"' in toml_text


def test_given_invalid_preset_when_init_then_argparse_rejects() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["init", "--preset", "nonexistent"])
    assert exc.value.code != 0
