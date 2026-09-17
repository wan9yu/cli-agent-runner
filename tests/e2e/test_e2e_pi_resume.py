"""Cross-round pi session resume, end-to-end on the remote pi host.

The first test drives the REAL `pi` CLI against a real provider to prove
resume is an actual, observable property (session file identity + growth +
conversational continuity) -- not merely `--session-id` sitting on argv (the
v0.3.3 mechanism-vs-property lesson). The second test uses a fast, local
stand-in binary (also named `pi`, so it joins the same registered
PluginManifest) to pin the phase-ambiguity guard without spending real
provider calls on it.

Skipped unless ``AGENT_RUNNER_E2E_PI=1`` is set. Tests use the `pi` ssh alias.
"""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Iterator

import pytest

from .conftest import _ssh

_POLL_INTERVAL_S = 0.5
_ROUND_TIMEOUT_S = 240
_PIN_RE = re.compile(r"PIN:\s*(\d{4})")


def _wait_until(predicate, timeout_s: float) -> bool:
    """Poll ``predicate`` (an ssh round-trip) until it's true or the deadline
    passes -- mirrors the sibling e2e modules' own copy (real ssh latency to
    the pi makes a fixed sleep either flaky or wastefully long)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_S)
    return predicate()


def _write_remote_file(remote_path: str, content: str) -> None:
    """base64-roundtrip a file's content onto the pi -- avoids shell-quoting
    hell for TOML/prompt bodies (the same trick conftest.py's fixtures use)."""
    encoded = base64.b64encode(content.encode()).decode()
    _ssh(f"echo '{encoded}' | base64 -d > {remote_path}")


def _round_num(pi_workdir: str) -> int:
    r = _ssh(f"cat {pi_workdir}/logs/status.json", check=False)
    if r.returncode != 0 or not r.stdout.strip():
        return 0
    try:
        return int(json.loads(r.stdout).get("round_num", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0


def _serve_pid_gone(pi_workdir: str) -> bool:
    return _ssh(f"test -f {pi_workdir}/logs/serve.pid", check=False).returncode != 0


def _pi_round_output(pi_workdir: str, round_num: int) -> str:
    """pi's own ``--mode json`` stream for a round.

    NOT ``logs/round-{N}.log`` (serve_cmd.py's ``round_log_path``) -- that file
    is the ``agent-runner round`` SUBPROCESS's own stdout/stderr, which never
    re-emits pi's output. The agent's real stream is redirected by
    ``agent_runtime.run`` to ``logs/rounds/R{N}-<timestamp>.log`` (see
    ``runner.py``'s ``log_path`` construction and ``round_view.py``'s own
    ``R{round_num}-*.log`` glob). ``ls -t | head -1`` picks the newest match
    for this round (there is normally exactly one)."""
    r = _ssh(
        f'cat "$(ls -t {pi_workdir}/logs/rounds/R{round_num}-*.log 2>/dev/null | head -1)" '
        "2>/dev/null",
        check=False,
    )
    return r.stdout


def _read_events(pi_workdir: str) -> list[dict]:
    text = _ssh(f"cat {pi_workdir}/logs/events-*.jsonl 2>/dev/null", check=False).stdout
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _session_snapshot() -> dict[str, int]:
    """``{path: size_bytes}`` for every pi session file on the host.

    Recursive over ``~/.pi/agent/sessions`` rather than computing pi's own
    cwd-normalization scheme (``--<normalized-cwd>--``) -- the exact encoding
    is an implementation detail of pi, not something this suite should
    reverse-engineer; diffing a before/after snapshot finds the file this
    test's own run touched just as reliably."""
    r = _ssh(
        "find ~/.pi/agent/sessions -type f -name '*.jsonl' -printf '%s %p\\n' 2>/dev/null || true",
        check=False,
    )
    files: dict[str, int] = {}
    for line in r.stdout.splitlines():
        size_str, _, path = line.partition(" ")
        if path and size_str.isdigit():
            files[path] = int(size_str)
    return files


def _last_assistant_text(round_log_text: str) -> str:
    """The final assistant message's text, out of a pi ``--mode json`` round
    log -- prefers ``agent_end`` (one record per agent run) and falls back to
    ``message_end``, mirroring ``builtin_plugins.pi._parse_pi_log``'s own
    preference (kept independent here: this test reads the log over ssh as
    plain text, never importing the production parser)."""
    from_agent_end: list[dict] = []
    from_message_end: list[dict] = []
    for line in round_log_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "agent_end":
            from_agent_end.extend(
                m
                for m in event.get("messages") or []
                if isinstance(m, dict) and m.get("role") == "assistant"
            )
        elif event.get("type") == "message_end":
            m = event.get("message")
            if isinstance(m, dict) and m.get("role") == "assistant":
                from_message_end.append(m)
    messages = from_agent_end or from_message_end
    if not messages:
        return ""
    content = messages[-1].get("content") or []
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


@pytest.fixture
def pi_resume_config(pi_workdir: str) -> str:
    """A real-pi config: ``schema_version = 1``, the pi preset's real command
    (resume active via the registered ``pi`` PluginManifest), ``work_dir`` the
    ``pi_workdir`` git repo. Distinct from the shipped ``pi_config`` fixture
    (conftest.py) -- that one points at a FAKE agent and omits
    ``schema_version``, either of which would sink this test: resume only
    activates for a binary actually named ``pi``, and the loader rejects a
    config with no ``schema_version``."""
    cfg_path = f"{pi_workdir}/agent-runner.toml"
    prompt_path = f"{pi_workdir}/p.md"
    cfg = (
        "schema_version = 1\n"
        "\n"
        "[agent]\n"
        'command = ["pi", "-p", "-na", "--mode", "json", "--model", "moonshot/kimi-k3"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "\n"
        "[agent.env]\n"
        # Per docs/recipes/pi.md this only suppresses pi's own startup
        # auto-update/catalog refresh and "does not block inference" -- but if
        # a live run ever shows no provider call happened (no PIN in the
        # round's pi output), try dropping this line before suspecting resume
        # itself.
        'PI_OFFLINE = "1"\n'
        "\n"
        "[runtime]\n"
        f'work_dir = "{pi_workdir}"\n'
        f'log_dir = "{pi_workdir}/logs"\n'
        "round_budget_s = 180\n"
        "\n"
        "[prompt]\n"
        f'file = "{prompt_path}"\n'
    )
    prompt_body = (
        "You are checked in with this SAME message on every round of one "
        "continuous session. If you have NOT already told me a PIN earlier in "
        "this conversation, invent a random 4-digit PIN, remember it, and "
        "reply with EXACTLY: PIN: 1234 (substituting your own 4 digits for "
        "1234). If you HAVE already told me a PIN earlier in this same "
        "conversation, reply again with EXACTLY that SAME PIN in the same "
        "format. Reply with nothing else -- no explanation."
    )
    _write_remote_file(prompt_path, prompt_body)
    _write_remote_file(cfg_path, cfg)
    _ssh(
        f"echo 'logs/' > {pi_workdir}/.gitignore && "
        f"cd {pi_workdir} && git add . && git -c commit.gpgsign=false commit -q -m fixture"
    )
    return cfg_path


def test_two_round_pi_run_should_resume_the_same_session_when_run_on_the_pi_host(
    pi_install_agent_runner: str,
    pi_workdir: str,
    pi_resume_config: str,
) -> None:
    baseline = _session_snapshot()
    bg = (
        f"nohup {pi_install_agent_runner} --config {pi_resume_config} serve "
        "--max-rounds 2 > /dev/null 2>&1 & echo $!"
    )
    pid = _ssh(bg).stdout.strip()

    try:
        assert _wait_until(lambda: _round_num(pi_workdir) >= 1, timeout_s=_ROUND_TIMEOUT_S), (
            "round 1 never completed"
        )
        after_r1 = _session_snapshot()

        assert _wait_until(lambda: _round_num(pi_workdir) >= 2, timeout_s=_ROUND_TIMEOUT_S), (
            "round 2 never completed"
        )
        assert _wait_until(lambda: _serve_pid_gone(pi_workdir), timeout_s=30), (
            "serve did not exit after --max-rounds 2"
        )
        after_r2 = _session_snapshot()
    finally:
        _ssh(f"kill {pid} 2>/dev/null || true", check=False)

    new_after_r1 = {p: s for p, s in after_r1.items() if p not in baseline}
    assert len(new_after_r1) == 1, f"expected exactly one new pi session file, got {new_after_r1}"
    session_path, size_r1 = next(iter(new_after_r1.items()))

    new_after_r2 = {p: s for p, s in after_r2.items() if p not in baseline}
    assert set(new_after_r2) == {session_path}, (
        f"round 2 should append to round 1's session file, not create a new one: {new_after_r2}"
    )
    assert new_after_r2[session_path] > size_r1, "session file did not grow between rounds"

    session_id = session_path.rsplit("/", 1)[-1].removesuffix(".jsonl")
    resumed_events = [e for e in _read_events(pi_workdir) if e.get("event") == "session_resumed"]
    assert len(resumed_events) == 1
    assert resumed_events[0].get("session_id") == session_id

    # Best-effort context-continuity check, NOT part of the verdict: (a)+(b)+
    # the session_resumed cross-check above already prove the session-sharing
    # property from the filesystem and agent-runner's own event log alone.
    # Whether a PIN is parseable at all depends on the live model's phrasing
    # and provider availability, so a miss here must never red-fail the test;
    # only a PARSEABLE-but-MISMATCHED pair is a genuine resume regression.
    round1_log = _pi_round_output(pi_workdir, 1)
    round2_log = _pi_round_output(pi_workdir, 2)
    pin1 = _PIN_RE.search(_last_assistant_text(round1_log))
    pin2 = _PIN_RE.search(_last_assistant_text(round2_log))
    if pin1 and pin2:
        assert pin1.group(1) == pin2.group(1), (
            "round 2 should recall round 1's PIN via resumed session context"
        )


@pytest.fixture
def pi_fake_pi_binary(pi_workdir: str) -> Iterator[tuple[str, str]]:
    """A deterministic stand-in for the real ``pi`` CLI, named literally
    ``pi`` -- ``resolve_resume_flag`` joins on ``Path(command[0]).name``, so
    any path ending in ``/pi`` matches the registered manifest without
    needing the real network-calling binary. Records every invocation's argv
    (to a file OUTSIDE the git work_dir, so the recorded lines survive
    [vcs]'s own orphan-stash handling of the round's working tree) so the
    phase-ambiguity test can assert ``--session-id`` never appears."""
    bin_path = f"{pi_workdir}/bin/pi"
    argv_log = f"{pi_workdir}.argv.log"
    body = f'#!/usr/bin/env bash\necho "$@" >> "{argv_log}"\nexit 0\n'
    _ssh(f"mkdir -p {pi_workdir}/bin")
    _write_remote_file(bin_path, body)
    _ssh(f"chmod +x {bin_path}")
    try:
        yield bin_path, argv_log
    finally:
        _ssh(f"rm -f {argv_log}", check=False)


@pytest.fixture
def pi_ambiguous_phase_config(pi_workdir: str, pi_fake_pi_binary: tuple[str, str]) -> str:
    """A 2-phase, self-rotating config (``phase_policy = "wait"``, no
    per-phase schedule) -- serve never resolves an explicit ``--phase`` for a
    round on this shape (``_phase_aware`` stays False), so every round's
    ``phase_arg`` is ``None`` while ``cfg.phases.list`` is non-empty: the
    phase-ambiguity guard in ``_apply_resume_env`` must cold-start every
    round rather than guess which rotated phase's session to resume."""
    bin_path, _argv_log = pi_fake_pi_binary
    cfg_path = f"{pi_workdir}/agent-runner.toml"
    prompt_path = f"{pi_workdir}/p.md"
    cfg = (
        "schema_version = 1\n"
        "\n"
        "[agent]\n"
        f'command = ["{bin_path}"]\n'
        'prompt_arg_template = ["{prompt}"]\n'
        "\n"
        "[runtime]\n"
        f'work_dir = "{pi_workdir}"\n'
        f'log_dir = "{pi_workdir}/logs"\n'
        "round_budget_s = 30\n"
        "\n"
        "[prompt]\n"
        f'file = "{prompt_path}"\n'
        "\n"
        "[phases]\n"
        'list = ["alpha", "beta"]\n'
        'phase_policy = "wait"\n'
    )
    _write_remote_file(prompt_path, "ping")
    _write_remote_file(cfg_path, cfg)
    _ssh(
        f"echo 'logs/' > {pi_workdir}/.gitignore && "
        f"cd {pi_workdir} && git add . && git -c commit.gpgsign=false commit -q -m fixture"
    )
    return cfg_path


def test_two_phase_self_rotating_config_should_cold_start_every_round_when_phase_ambiguous(
    pi_install_agent_runner: str,
    pi_workdir: str,
    pi_fake_pi_binary: tuple[str, str],
    pi_ambiguous_phase_config: str,
) -> None:
    _bin_path, argv_log = pi_fake_pi_binary
    cmd = f"{pi_install_agent_runner} --config {pi_ambiguous_phase_config} serve --max-rounds 2"

    r = _ssh(cmd, check=False, timeout=60)

    assert r.returncode == 0, r.stderr
    argv_lines = [
        line for line in _ssh(f"cat {argv_log}", check=False).stdout.splitlines() if line.strip()
    ]
    assert len(argv_lines) == 2, f"expected 2 fake-pi invocations, got {argv_lines}"
    assert not any("--session-id" in line for line in argv_lines), argv_lines
    events = _read_events(pi_workdir)
    assert not any(e.get("event") == "session_resumed" for e in events), events
