"""Unit tests for the managed ssh event relay (``monitor --host X --mode events``).

No test here spawns a real ssh at a real host: ``remote_relay._SSH`` is pointed
at a bash stub that plays the part (emits JSONL, exits, records its argv).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

import pytest

from agent_runner import remote_relay
from agent_runner.api_types import Alert
from agent_runner.monitor import on_alert

REPO_ROOT = Path(__file__).resolve().parents[2]

_LINE_1 = '{"event":"round_start","ts":"2026-07-27T10:00:00.000Z","round_num":1}'
_LINE_2 = '{"event":"round_end","ts":"2026-07-27T10:00:01.000Z","round_num":1}'


def _write_stub(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}")
    path.chmod(0o755)
    return path


@pytest.fixture
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_relay, "_RECONNECT_BACKOFF_S", (0.01,))


@pytest.fixture
def no_term_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the SIGTERM handler ``relay_remote_events`` installs on
    every call. Behavioral tests below call it in THIS process (no
    subprocess), and ``signal.signal`` is a raw OS call monkeypatch cannot
    auto-revert — leaving the real handler armed would permanently rewire
    this pytest worker's SIGTERM disposition for the rest of the session.
    Only the dedicated SIGTERM tests want the real handler installed."""
    monkeypatch.setattr(remote_relay, "_install_term_handler", lambda: None)


def _events(log_dir: Path) -> list[dict]:
    out: list[dict] = []
    for f in sorted(log_dir.glob("events-*.jsonl")):
        out += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return out


# ---------------------------------------------------------------------------
# argv construction
# ---------------------------------------------------------------------------


def test_given_no_kinds_when_argv_built_then_defaults_to_every_known_kind() -> None:
    """The documented default: every kind this client knows, comma-joined."""
    from agent_runner.events import KNOWN_EVENT_KINDS

    argv = remote_relay._remote_argv("pi", remote_relay.default_kinds(), None, since=None)
    assert argv[:3] == ["ssh", "pi", "--"]
    assert argv[3:6] == ["agent-runner", "events", "--tail"]
    assert argv[6] == "--kind"
    assert argv[7].split(",") == sorted(KNOWN_EVENT_KINDS)
    assert "--since" not in argv, "first connect streams from now"
    assert "--config" not in argv, "no --remote-config: remote resolves ./agent-runner.toml"


def test_given_explicit_kinds_and_remote_config_when_argv_built_then_passed_through() -> None:
    argv = remote_relay._remote_argv(
        "pi",
        ["round_end", "oauth_fail"],
        "/srv/proj/agent-runner.toml",
        since="2026-07-27T10:00:00.000Z",
    )
    assert "round_end,oauth_fail" in argv
    assert argv[argv.index("--since") + 1] == "2026-07-27T10:00:00.000Z"
    assert argv[argv.index("--config") + 1] == "/srv/proj/agent-runner.toml"


def test_given_host_starting_with_dash_when_relay_then_value_error(
    tmp_path: Path, no_term_handler: None
) -> None:
    """A leading '-' would be read by ssh as an option (-oProxyCommand=...)."""
    with pytest.raises(ValueError, match="starts with '-'"):
        remote_relay.relay_remote_events("-oProxyCommand=touch /tmp/x", log_dir=tmp_path)


# ---------------------------------------------------------------------------
# passthrough + resume
# ---------------------------------------------------------------------------


def test_given_stub_ssh_when_relayed_then_lines_identical_and_reconnect_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_backoff: None, no_term_handler: None
) -> None:
    """Lines pass through byte-identically; every RE-connect carries --since <max ts>."""
    argv_log = tmp_path / "argv.log"
    state = tmp_path / "first-run-done"
    stub = _write_stub(
        tmp_path / "fake-ssh",
        f'printf "%s\\n" "$*" >> "{argv_log}"\n'
        f'if [ ! -f "{state}" ]; then\n'
        f'  touch "{state}"\n'
        f"  printf '%s\\n' '{_LINE_1}'\n"
        f"  printf '%s\\n' '{_LINE_2}'\n"
        f"fi\n"
        f"exit 1\n",
    )
    monkeypatch.setattr(remote_relay, "_SSH", str(stub))
    log_dir = tmp_path / "logs"
    out = StringIO()

    rc = remote_relay.relay_remote_events(
        "pi", log_dir=log_dir, kinds=["round_start", "round_end"], failure_tolerance_s=0.05, out=out
    )

    assert rc == 1, "the stub never recovers, so the relay eventually gives up"
    assert out.getvalue() == f"{_LINE_1}\n{_LINE_2}\n"

    invocations = argv_log.read_text().splitlines()
    assert len(invocations) >= 2, "relay must have reconnected at least once"
    assert "--since" not in invocations[0], "first connect streams from now"
    for argv in invocations[1:]:
        assert "--since 2026-07-27T10:00:01.000Z" in argv, "reconnect replays from the last ts seen"

    kinds = _events(log_dir)
    assert [e["event"] for e in kinds if e["event"] == "monitor_remote_blip"], "no blip emitted"
    assert sum(1 for e in kinds if e["event"] == "monitor_remote_giveup") == 1
    blip = next(e for e in kinds if e["event"] == "monitor_remote_blip")
    assert blip["host"] == "pi"
    assert blip["returncode"] == 1


def test_given_malformed_line_when_relayed_then_passed_through_and_resume_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fast_backoff: None, no_term_handler: None
) -> None:
    """Garbage is relayed verbatim but never becomes the resume point."""
    argv_log = tmp_path / "argv.log"
    state = tmp_path / "first-run-done"
    stub = _write_stub(
        tmp_path / "fake-ssh",
        f'printf "%s\\n" "$*" >> "{argv_log}"\n'
        f'if [ ! -f "{state}" ]; then\n'
        f'  touch "{state}"\n'
        f"  printf '%s\\n' 'not json at all'\n"
        f'  printf \'%s\\n\' \'{{"event":"x","ts":"not-a-timestamp"}}\'\n'
        f"fi\n"
        f"exit 1\n",
    )
    monkeypatch.setattr(remote_relay, "_SSH", str(stub))
    out = StringIO()

    rc = remote_relay.relay_remote_events(
        "pi", log_dir=tmp_path / "logs", kinds=["x"], failure_tolerance_s=0.05, out=out
    )

    assert rc == 1
    assert out.getvalue() == 'not json at all\n{"event":"x","ts":"not-a-timestamp"}\n'
    for argv in argv_log.read_text().splitlines():
        assert "--since" not in argv, "unparseable ts must not become a resume point"


def test_given_ssh_that_always_fails_when_relayed_then_gives_up_and_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fast_backoff: None,
    no_term_handler: None,
    capsys,
) -> None:
    stub = _write_stub(
        tmp_path / "fake-ssh", 'echo "ssh: connect: no route to host" >&2\nexit 255\n'
    )
    monkeypatch.setattr(remote_relay, "_SSH", str(stub))
    log_dir = tmp_path / "logs"

    rc = remote_relay.relay_remote_events(
        "pi", log_dir=log_dir, kinds=["round_end"], failure_tolerance_s=0.05, out=StringIO()
    )

    assert rc == 1
    giveups = [e for e in _events(log_dir) if e["event"] == "monitor_remote_giveup"]
    assert len(giveups) == 1
    assert giveups[0]["host"] == "pi"
    assert giveups[0]["returncode"] == 255
    assert "no route to host" in giveups[0]["final_error"], "ssh stderr belongs in the event"

    err = capsys.readouterr().err
    assert "gave up" in err and "no route to host" in err, "a terminal give-up must not be silent"


def test_given_zero_tolerance_when_ssh_exits_then_gives_up_without_reconnecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_term_handler: None
) -> None:
    """tolerance 0 disables reconnection: one shot, no blip."""
    argv_log = tmp_path / "argv.log"
    stub = _write_stub(tmp_path / "fake-ssh", f'printf "%s\\n" "$*" >> "{argv_log}"\nexit 1\n')
    monkeypatch.setattr(remote_relay, "_SSH", str(stub))
    log_dir = tmp_path / "logs"

    rc = remote_relay.relay_remote_events(
        "pi", log_dir=log_dir, kinds=["round_end"], failure_tolerance_s=0, out=StringIO()
    )

    assert rc == 1
    assert len(argv_log.read_text().splitlines()) == 1
    kinds = [e["event"] for e in _events(log_dir)]
    assert kinds == ["monitor_remote_giveup"]


# ---------------------------------------------------------------------------
# process hygiene
# ---------------------------------------------------------------------------


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover — not expected for our own children
        return True
    return True


def test_install_term_handler_raises_keyboardinterrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-level check of the handler itself: SIGTERM must convert to
    KeyboardInterrupt so it drains through the SAME path SIGINT already gets
    from Python's own default handler -- not Python's default SIGTERM
    disposition (immediate termination). Same technique as
    ``round_cmd._install_term_handler``; see the subprocess test below for
    proof the whole relay actually drains when really sent SIGTERM."""
    captured = {}
    monkeypatch.setattr(signal, "signal", lambda s, h: captured.__setitem__(s, h))
    remote_relay._install_term_handler()
    with pytest.raises(KeyboardInterrupt):
        captured[signal.SIGTERM](signal.SIGTERM, None)


@pytest.mark.timeout(90)  # see the wait(timeout=45) comment below for the arithmetic
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_given_stop_signal_when_relaying_then_ssh_process_group_is_killed(
    tmp_path: Path, sig: signal.Signals
) -> None:
    """The orphan-tree scar: both stop signals must take out ssh AND its
    children via the relay's normal drain (tear down the ssh group, then exit
    0), not Python's own default disposition for that signal. SIGINT got this
    for free from Python's built-in KeyboardInterrupt default; SIGTERM needed
    this task's explicit handler -- without it, SIGTERM kills the driver
    immediately (rc -15, not 0) and orphans the stub plus its backgrounded
    sleep, which is exactly what this parametrization would catch."""
    stub = _write_stub(
        tmp_path / "fake-ssh",
        "sleep 300 &\n"
        'printf \'{"event":"probe","ts":"2026-07-27T10:00:00.000Z",'
        '"stub_pid":%s,"sleep_pid":%s}\n\' $$ $!\n'
        "wait\n",
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        # A background job (`cmd &` from a non-interactive shell) inherits
        # SIGINT=SIG_IGN, which CPython propagates across exec and leaves in
        # place at startup (no default KeyboardInterrupt handler installed).
        # Force the default disposition here so a SIGINT this test sends is
        # deliverable regardless of how the test harness itself was launched
        # (irrelevant to the SIGTERM parametrization, harmless either way).
        "import signal\n"
        "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
        "import sys\n"
        "from pathlib import Path\n"
        "from agent_runner import remote_relay\n"
        "remote_relay._SSH = sys.argv[1]\n"
        "sys.exit(remote_relay.relay_remote_events('pi', log_dir=Path(sys.argv[2])))\n"
    )
    proc = subprocess.Popen(
        [sys.executable, str(driver), str(stub), str(tmp_path / "logs")],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    stub_pid: int | None = None
    try:
        assert proc.stdout is not None
        deadline = time.time() + 15
        line = ""
        while time.time() < deadline and not line:
            line = proc.stdout.readline()
        payload = json.loads(line)
        stub_pid, sleep_pid = payload["stub_pid"], payload["sleep_pid"]
        assert _alive(sleep_pid), "stub's child should be running before the interrupt"

        os.kill(proc.pid, sig)
        # The driver's own teardown (relay_remote_events's finally -> _kill_pgroup)
        # has an intrinsic real-wall-clock floor of REAP_GRACE_S(5) + the
        # post-SIGKILL proc.wait(10) = 15s BEFORE any scheduling delay at all --
        # confirmed by reproduction: under `-n auto` combined with heavy host
        # contention (co-tenant parallel suites), this consistently raised
        # subprocess.TimeoutExpired at exactly the old 15s bound (zero headroom
        # over that floor). 45s (3x the floor) gives real margin for the
        # interpreter to even get scheduled to run the teardown; the per-test
        # `@pytest.mark.timeout(90)` above keeps this test's own generous
        # budget independent of the suite's shared 60s ceiling (15s probe-read
        # + 45s here + 10s liveness-poll below = 70s, safely under 90).
        assert proc.wait(timeout=45) == 0, f"{sig.name} must be a clean, draining relay shutdown"

        deadline = time.time() + 10
        while time.time() < deadline and (_alive(stub_pid) or _alive(sleep_pid)):
            time.sleep(0.1)
        assert not _alive(stub_pid), "ssh stub survived the relay"
        assert not _alive(sleep_pid), "ssh stub's child survived the relay (orphan tree)"
    finally:
        if proc.poll() is None:  # pragma: no cover — only on assertion failure
            proc.kill()
            proc.wait(timeout=5)
        # Belt-and-suspenders: if the driver died/timed out before its OWN
        # relay_remote_events -> _kill_pgroup teardown ran (e.g. an
        # assertion above fired first), the ssh stub + its backgrounded
        # `sleep 300` are orphaned (reparented to init) and would otherwise
        # sit around consuming a pid/process slot for up to 5 real minutes,
        # adding to any concurrent contention. stub_pid is its own session
        # leader (start_new_session=True in the stub's spawn), so killing its
        # pgroup takes the sleep child with it.
        if stub_pid is not None and _alive(stub_pid):  # pragma: no cover
            try:
                os.killpg(stub_pid, signal.SIGKILL)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# auto-stop is on-host only
# ---------------------------------------------------------------------------


def test_given_alert_with_no_auto_action_when_on_alert_then_does_nothing(tmp_path: Path) -> None:
    a = Alert(
        severity="warning",
        detector="timeout_rate",
        message="m",
        context={},
        ts="t",
        auto_action="none",
    )
    from unittest.mock import patch

    with patch("agent_runner.monitor._call_local_stop") as stop:
        on_alert(a, project="myproj", log_dir=tmp_path)
        stop.assert_not_called()


def test_given_critical_alert_when_on_alert_then_calls_local_stop(tmp_log_dir: Path) -> None:
    a = Alert(
        severity="critical",
        detector="oauth_fail",
        message="m",
        context={},
        ts="t",
        auto_action="stop_service",
    )
    from unittest.mock import patch

    with patch("agent_runner.monitor._call_local_stop") as stop:
        on_alert(a, project="myproj", log_dir=tmp_log_dir)
        stop.assert_called_once_with("myproj")


def test_given_local_stop_raises_when_on_alert_then_emits_failed_event(tmp_log_dir: Path) -> None:
    """A failing stop is recorded, not raised — it must not kill the monitor."""
    from unittest.mock import patch

    a = Alert(
        severity="critical",
        detector="oauth_fail",
        message="m",
        context={},
        ts="t",
        auto_action="stop_service",
    )
    with patch("agent_runner.monitor._call_local_stop", side_effect=RuntimeError("unit missing")):
        on_alert(a, project="myproj", log_dir=tmp_log_dir)

    failed = [e for e in _events(tmp_log_dir) if e["event"] == "monitor_auto_stop_failed"]
    assert len(failed) == 1
    assert failed[0]["detector"] == "oauth_fail"
    assert "unit missing" in failed[0]["error"]


def test_given_local_stop_no_ops_without_raising_when_on_alert_then_emits_failed_not_triggered(
    tmp_log_dir: Path,
) -> None:
    """The real gap this closes: a mode mismatch (stale unit reference, wrong
    pidfile) makes api.stop a SILENT no-op -- it returns normally with
    active still True, it never raises. Before this fix, on_alert emitted
    monitor_auto_stop_triggered unconditionally BEFORE even calling stop, so
    this exact case was misreported as a successful stop and the `except`
    branch never even had a chance to fire."""
    from unittest.mock import patch

    from agent_runner.api_types import ServiceMode, ServiceStatus

    a = Alert(
        severity="critical",
        detector="oauth_fail",
        message="m",
        context={},
        ts="t",
        auto_action="stop_service",
    )
    still_running = ServiceStatus(mode=ServiceMode.SYSTEMD_USER, active=True)
    with patch("agent_runner.monitor._call_local_stop", return_value=still_running) as stop:
        on_alert(a, project="myproj", log_dir=tmp_log_dir)
        stop.assert_called_once_with("myproj")

    kinds = [e["event"] for e in _events(tmp_log_dir)]
    assert "monitor_auto_stop_failed" in kinds
    assert "monitor_auto_stop_triggered" not in kinds
    failed = [e for e in _events(tmp_log_dir) if e["event"] == "monitor_auto_stop_failed"]
    assert failed[0]["detector"] == "oauth_fail"


def test_given_local_stop_confirms_stopped_when_on_alert_then_emits_triggered_not_failed(
    tmp_log_dir: Path,
) -> None:
    """The success path: api.stop returns active=False -> triggered fires,
    failed does not."""
    from unittest.mock import patch

    from agent_runner.api_types import ServiceMode, ServiceStatus

    a = Alert(
        severity="critical",
        detector="oauth_fail",
        message="m",
        context={},
        ts="t",
        auto_action="stop_service",
    )
    stopped = ServiceStatus(mode=ServiceMode.PID_FILE, active=False)
    with patch("agent_runner.monitor._call_local_stop", return_value=stopped):
        on_alert(a, project="myproj", log_dir=tmp_log_dir)

    kinds = [e["event"] for e in _events(tmp_log_dir)]
    assert "monitor_auto_stop_triggered" in kinds
    assert "monitor_auto_stop_failed" not in kinds


def test_given_pid_file_stop_with_live_serve_pid_when_on_alert_then_draining_no_event_recorded(
    tmp_log_dir: Path,
) -> None:
    """The flake this closes, broadened (M-2/M-3): api.stop's PID_FILE confirm
    window can legitimately elapse with active=True while serve is still
    alive and will exit once it next checks for the SIGTERM -- whether it is
    mid-round (the documented graceful-stop contract), still bootstrapping
    that round (hasn't written its lock-holder sidecar yet), or asleep in its
    restart back-off between rounds. A LIVE serve pid alone (no round holder
    needed) is proof of that, not of a silent no-op, so on_alert must record
    neither failed (a false alarm for a stop that is working) nor triggered
    (not actually confirmed yet) -- it returns "draining" instead, so its
    caller (_monitor_loop_iter) hands this alert to on_alert again on the very
    next poll (see test_monitor_dedup_rearm.py), by which point the drain has
    usually resolved one way or the other."""
    from unittest.mock import patch

    from agent_runner.api_types import ServiceMode, ServiceStatus

    a = Alert(
        severity="critical",
        detector="oauth_fail",
        message="m",
        context={},
        ts="t",
        auto_action="stop_service",
    )
    still_draining = ServiceStatus(mode=ServiceMode.PID_FILE, active=True)
    with patch("agent_runner.monitor._call_local_stop", return_value=still_draining):
        verdict = on_alert(a, project="myproj", log_dir=tmp_log_dir)

    assert verdict == "draining"
    kinds = [e["event"] for e in _events(tmp_log_dir)]
    assert "monitor_auto_stop_failed" not in kinds
    assert "monitor_auto_stop_triggered" not in kinds
