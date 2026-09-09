"""Tests for agent-self-terminated sentinel detection in serve_cmd."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import FakeArgs, make_toml


def test_stale_sentinel_should_be_cleaned_when_serve_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    sentinel = log_dir / ".agent-done"
    sentinel.write_text("stale")

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        round_log_path.write_text("")
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    serve_cmd.cmd(FakeArgs(cfg_path))

    assert not sentinel.exists()


def test_serve_should_break_loop_and_exit_0_when_sentinel_present_pre_round(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sentinel is written during round 1 but checked only at the TOP of the
    next loop iteration -- the first round completes, then iteration 2 finds
    the sentinel and breaks, so the round spawn is invoked exactly once."""
    import json

    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    sentinel = log_dir / ".agent-done"
    call_count = [0]

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        # After first round, write sentinel so the SECOND iteration finds it.
        call_count[0] += 1
        round_log_path.write_text("")
        if call_count[0] == 1:
            sentinel.write_text("research wrapped up")
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    assert rc == 0
    assert call_count[0] == 1  # second round NOT invoked

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    terminated = [p for p in payloads if p["event"] == "agent_self_terminated"]
    assert len(terminated) == 1
    assert terminated[0]["reason"] == "research wrapped up"


def test_serve_should_stop_with_empty_reason_when_sentinel_file_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    sentinel = log_dir / ".agent-done"
    call_count = [0]

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        call_count[0] += 1
        round_log_path.write_text("")
        if call_count[0] == 1:
            sentinel.write_text("")
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    assert rc == 0

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    terminated = [p for p in payloads if p["event"] == "agent_self_terminated"]
    assert len(terminated) == 1
    assert terminated[0]["reason"] == ""


def test_serve_should_cap_reason_to_200_chars_when_sentinel_reason_long(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    sentinel = log_dir / ".agent-done"
    long_reason = "x" * 500
    call_count = [0]

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        call_count[0] += 1
        round_log_path.write_text("")
        if call_count[0] == 1:
            sentinel.write_text(long_reason)
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    terminated = [p for p in payloads if p["event"] == "agent_self_terminated"]
    assert len(terminated) == 1
    assert len(terminated[0]["reason"]) == 200
    assert terminated[0]["reason"] == "x" * 200


def test_serve_should_not_crash_when_sentinel_contains_non_utf8_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read with errors='replace' rather than raising UnicodeDecodeError."""
    import json

    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    sentinel = log_dir / ".agent-done"
    call_count = [0]

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        call_count[0] += 1
        round_log_path.write_text("")
        if call_count[0] == 1:
            sentinel.write_bytes(b"\xff\xfe invalid utf-8")
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    rc = serve_cmd.cmd(FakeArgs(cfg_path, once=False))

    assert rc == 0

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    terminated = [p for p in payloads if p["event"] == "agent_self_terminated"]
    assert len(terminated) == 1
    assert terminated[0]["reason"] == b"\xff\xfe invalid utf-8".decode("utf-8", errors="replace")


def test_serve_should_pass_log_dir_env_when_round_subprocess_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_runner.cli import serve_cmd

    cfg_path = make_toml(tmp_path)
    log_dir = tmp_path / "logs"
    captured_env = {}

    def fake_spawn(round_argv, round_log_path, round_env, *, timeout_s, **_kwargs):
        captured_env.update(round_env)
        round_log_path.write_text("")
        return 0

    monkeypatch.setattr(serve_cmd, "_spawn_round", fake_spawn)

    serve_cmd.cmd(FakeArgs(cfg_path))

    assert captured_env.get("AGENT_RUNNER_LOG_DIR") == str(log_dir)
