from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from agent_runner.api_types import ServiceMode
from agent_runner.lifecycle import (
    PIDFile,
    detect_service_mode,
    pid_alive,
    send_signal_to_pid,
)


def test_pid_file_should_round_trip_when_written_and_read(tmp_path: Path) -> None:
    pf = PIDFile(tmp_path / "p.pid")

    pf.write(12345)

    assert pf.read() == 12345

    pf.unlink()

    assert pf.read() is None


def test_pid_file_should_not_raise_when_unlinking_missing_file(tmp_path: Path) -> None:
    PIDFile(tmp_path / "absent.pid").unlink()  # must not raise


def test_pid_file_should_return_none_when_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "bad.pid"
    p.write_text("not-a-pid")

    assert PIDFile(p).read() is None


def test_pid_file_should_return_pid_when_start_time_token_matches_live_process(
    tmp_path: Path,
) -> None:
    pf = PIDFile(tmp_path / "p.pid")
    pf.write(os.getpid())  # captures this process's real start-time

    assert pf.read() == os.getpid()  # token matches the live process


def test_pid_file_should_return_none_when_start_time_token_mismatches(tmp_path: Path) -> None:
    """Same PID, different process (OS recycled it after a crash) → the start-time
    token won't match, so read() reports it as not-running (no stray signal)."""
    p = tmp_path / "p.pid"
    p.write_text(json.dumps({"pid": os.getpid(), "create_time": 1.0}))  # bogus old start-time

    assert PIDFile(p).read() is None


def test_pid_file_should_return_pid_when_legacy_format_has_no_token(tmp_path: Path) -> None:
    """A pre-0.2.11 bare-int file has no token → returned unverified (back-compat)."""
    p = tmp_path / "p.pid"
    p.write_text(str(os.getpid()))

    assert PIDFile(p).read() == os.getpid()


def test_pid_file_should_return_none_when_legacy_value_is_bool_true(tmp_path: Path) -> None:
    """json `true` is an int subclass — must NOT slip through as pid 1."""
    p = tmp_path / "p.pid"
    p.write_text("true")

    assert PIDFile(p).read() is None


def test_pid_file_should_return_none_when_legacy_pid_is_one(tmp_path: Path) -> None:
    """pid 1 is init — never a serve process we started."""
    p = tmp_path / "p.pid"
    p.write_text("1")

    assert PIDFile(p).read() is None


def test_pid_file_should_return_none_when_dict_pid_is_bool_true(tmp_path: Path) -> None:
    p = tmp_path / "p.pid"
    p.write_text(json.dumps({"pid": True}))

    assert PIDFile(p).read() is None


def test_pid_file_should_return_none_when_dict_pid_is_one(tmp_path: Path) -> None:
    p = tmp_path / "p.pid"
    p.write_text(json.dumps({"pid": 1}))

    assert PIDFile(p).read() is None


def test_pid_alive_should_return_true_when_pid_is_running() -> None:
    assert pid_alive(os.getpid()) is True


def test_pid_alive_should_return_false_when_pid_is_dead() -> None:
    p = subprocess.Popen(["true"])
    p.wait()
    time.sleep(0.05)

    assert pid_alive(p.pid) is False


def test_send_signal_to_pid_should_return_false_when_pid_is_invalid() -> None:
    assert send_signal_to_pid(999999999, signal.SIGTERM) is False


def test_detect_service_mode_should_return_none_when_no_systemd_unit_and_no_pidfile(
    tmp_path: Path,
) -> None:
    assert detect_service_mode("nonexistent-project", log_dir=tmp_path) == ServiceMode.NONE


def test_detect_service_mode_should_return_pid_file_when_pid_file_present(tmp_path: Path) -> None:
    (tmp_path / "serve.pid").write_text(str(os.getpid()))

    assert detect_service_mode("p", log_dir=tmp_path) == ServiceMode.PID_FILE


def test_detect_service_mode_should_return_systemd_user_when_unit_file_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_systemd = tmp_path / "systemd-user"
    fake_systemd.mkdir()
    (fake_systemd / "agent-runner@myproj.service").write_text("[Unit]\n")
    monkeypatch.setattr(
        "agent_runner.lifecycle._user_systemd_dir",
        lambda: fake_systemd,
    )

    assert detect_service_mode("myproj", log_dir=tmp_path) == ServiceMode.SYSTEMD_USER
