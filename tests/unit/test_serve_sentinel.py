"""Tests for agent-self-terminated sentinel detection in serve_cmd."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_helpers import FakeArgs, make_toml


def test_given_stale_sentinel_when_serve_starts_then_cleaned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stale .agent-done from previous run is removed at serve startup."""
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


def test_given_sentinel_present_pre_round_when_serve_then_break_loop_exit_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sentinel found before round invocation → break, emit, exit 0."""
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
    # Sentinel is written during round 1 but checked only at the TOP of the next
    # loop iteration — the first round completes, then iteration 2 finds the
    # sentinel and breaks. So the round spawn is invoked exactly once.
    assert call_count[0] == 1  # second round NOT invoked

    events_files = sorted(log_dir.glob("events-*.jsonl"))
    payloads = [json.loads(line) for line in events_files[-1].read_text().splitlines()]
    terminated = [p for p in payloads if p["event"] == "agent_self_terminated"]
    assert len(terminated) == 1
    assert terminated[0]["reason"] == "research wrapped up"


def test_given_empty_sentinel_when_serve_then_still_stops_with_empty_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty .agent-done file still triggers stop; reason is empty string."""
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


def test_given_long_reason_when_serve_then_event_payload_capped_200(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reason text >200 chars is truncated to 200 in the event payload."""
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


def test_given_non_utf8_sentinel_when_serve_then_handled_with_replace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-UTF-8 bytes in .agent-done → read with errors='replace', still triggers stop."""
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
    assert "reason" in terminated[0]


def test_given_serve_running_round_when_subprocess_invoked_then_env_has_log_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round subprocess receives AGENT_RUNNER_LOG_DIR in its env."""
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
