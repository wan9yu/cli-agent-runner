"""The child-read half of cross-round resume: the round child never mints or
derives a session id -- serve mints it once and publishes it via
AGENT_RUNNER_RESUME_FLAG / AGENT_RUNNER_RESUME_SESSION_ID (the publish side is
a later change), and runner._resolve_resume_args copies it verbatim into the
argv agent_runtime.run appends. Env absent (systemd / standalone `round`) is
cold-start by construction: [] on either var missing, never a partial append.
"""

from __future__ import annotations

from agent_runner import agent_runtime, runner


def test_resolve_resume_args_should_return_flag_and_id_when_both_env_present(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_RESUME_FLAG", "--session-id")
    monkeypatch.setenv("AGENT_RUNNER_RESUME_SESSION_ID", "abc-123")

    assert runner._resolve_resume_args() == ["--session-id", "abc-123"]


def test_resolve_resume_args_should_return_empty_when_either_env_absent(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_RESUME_FLAG", "--session-id")
    monkeypatch.delenv("AGENT_RUNNER_RESUME_SESSION_ID", raising=False)

    assert runner._resolve_resume_args() == []


def test_resolve_resume_args_should_return_empty_when_both_env_absent(monkeypatch):
    monkeypatch.delenv("AGENT_RUNNER_RESUME_FLAG", raising=False)
    monkeypatch.delenv("AGENT_RUNNER_RESUME_SESSION_ID", raising=False)

    assert runner._resolve_resume_args() == []  # cold-start by construction


def test_run_should_append_resume_args_after_command_and_before_prompt_args(tmp_path):
    argv_dump = tmp_path / "argv.txt"
    script = tmp_path / "fake-agent.sh"
    script.write_text(
        f'#!/bin/bash\nprintf \'%s\\n\' "$@" > "{argv_dump}"\nexit 0\n',
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = agent_runtime.run(
        command=[str(script)],
        prompt_arg_template=["--prompt", "{prompt}"],
        prompt="hello",
        timeout_s=30,
        work_dir=tmp_path,
        log_path=tmp_path / "round.log",
        env_extra={},
        resume_args=("--session-id", "sid-9"),
    )

    assert result.exit_code == 0
    assert argv_dump.read_text(encoding="utf-8").splitlines() == [
        "--session-id",
        "sid-9",
        "--prompt",
        "hello",
    ]
