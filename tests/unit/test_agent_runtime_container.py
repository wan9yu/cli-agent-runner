"""Container-orphan defense: a configured `docker run` / `podman run` command's
container process double-fork-detaches out of the pgroup `_kill_pgroup`
signals, so a killpg-based hard-wall can silently leave the container itself
running. Detection + a best-effort `stop` + a loud on_container_orphan_risk
report are exercised here with a FAKE docker/podman stub on PATH — no real
container runtime needed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_runner import agent_runtime
from agent_runner.agent_runtime import (
    _cidfile_flag_value,
    _command_has_cidfile_flag,
    _detect_container_run,
    run,
)

_FAKE_CID = "a" * 64  # a valid [0-9a-f]{64} container id


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["docker", "run", "image"], True),
        (["podman", "run", "image"], True),
        (["/usr/bin/docker", "run", "--rm", "image"], True),
        (["/usr/local/bin/podman", "run", "image"], True),
        (["docker", "build", "."], False),
        (["docker", "ps"], False),
        (["docker"], False),
        ([], False),
        (["claude"], False),
        (["not-docker-but-similar", "run"], False),
        (["dockerized-thing", "run"], False),  # basename must be exactly docker/podman
        # The 4 bypass forms a too-literal detector would miss (review fix 1):
        (["docker", "-H", "unix:///var/run/docker.sock", "run", "image"], True),
        (["docker", "--context", "foo", "run", "image"], True),
        (["sudo", "docker", "run", "image"], True),
        (["env", "X=1", "docker", "run", "image"], True),
        # A couple of realistic variations on the same wrapper/global-flag shapes.
        (["sudo", "-u", "root", "docker", "run", "image"], True),
        (["sudo", "-E", "podman", "run", "image"], True),
        (["env", "-i", "X=1", "Y=2", "docker", "run", "image"], True),
        (["docker", "--context=foo", "run", "image"], True),
        # A non-run subcommand behind a wrapper/global flag must still say False.
        (["sudo", "docker", "ps"], False),
        (["docker", "-H", "unix:///var/run/docker.sock", "ps"], False),
    ],
)
def test_detect_container_run_should_recognize_run_commands(
    command: list[str], expected: bool
) -> None:
    assert (_detect_container_run(command) is not None) is expected


@pytest.mark.parametrize(
    ("command", "run_idx"),
    [
        (["docker", "run", "image"], 1),
        (["docker", "-H", "unix:///var/run/docker.sock", "run", "image"], 3),
        (["docker", "--context", "foo", "run", "image"], 3),
        (["sudo", "docker", "run", "image"], 2),
        (["env", "X=1", "docker", "run", "image"], 3),
    ],
)
def test_detect_container_run_index_should_be_true_only_for_unwrapped_unflagged_form(
    command: list[str], run_idx: int
) -> None:
    """run()'s --cidfile injection only fires when run_idx == 1 (the simple,
    unwrapped `docker run ...` shape) -- this pins the index _detect_container_run
    reports for each bypass form so that conservative gate stays provably correct
    as the detector above grows broader."""
    detected = _detect_container_run(command)

    assert detected is not None
    assert detected[1] == run_idx
    assert (run_idx == 1) == (command[0] in ("docker", "podman") and command[1] == "run")


def test_cidfile_flag_value_should_scan_only_the_options_block_not_container_args() -> None:
    """Review fix 3: the scan must stay inside docker's own OPTIONS block
    (between `run` and IMAGE) -- an operator-supplied --cidfile IS found
    there, but a `--cidfile`-looking token belonging to the CONTAINERIZED
    PROGRAM's own args (after IMAGE) must never be mistaken for it."""
    real_docker_flag = ["docker", "run", "--cidfile", "/host/real.cid", "--rm", "image"]

    assert _cidfile_flag_value(real_docker_flag, 1) == "/host/real.cid"

    containers_own_arg = ["docker", "run", "--rm", "image", "--cidfile", "/container/internal/path"]

    assert _cidfile_flag_value(containers_own_arg, 1) is None


def test_command_has_cidfile_flag_should_catch_operator_cidfile_past_untabled_flag() -> None:
    """The injection guard's blind spot + its fix. ``_cidfile_flag_value``'s
    walk stops one token early at a value-flag NOT in
    ``_DOCKER_RUN_FLAGS_WITH_VALUE`` (e.g. ``--cpu-quota``), mistaking its value
    for IMAGE, so it MISSES an operator ``--cidfile`` that follows -- returning
    None. Left there, run() would inject a SECOND ``--cidfile``.
    ``_command_has_cidfile_flag`` scans position-independently and finds it, so
    injection is suppressed instead of emitting a duplicate."""
    past_untabled = ["docker", "run", "--cpu-quota", "50000", "--cidfile", "/op.cid", "image"]

    assert _cidfile_flag_value(past_untabled, 1) is None  # the blind spot
    assert _command_has_cidfile_flag(past_untabled, 1) is True  # closed by the guard

    tabled = ["docker", "run", "--cidfile", "/op.cid", "--rm", "image"]

    assert _cidfile_flag_value(tabled, 1) == "/op.cid"
    assert _command_has_cidfile_flag(tabled, 1) is True

    none_present = ["docker", "run", "--rm", "image"]

    assert _command_has_cidfile_flag(none_present, 1) is False


def _write_fake_runtime(bin_dir: Path, name: str) -> Path:
    """A fake docker/podman stub: `run` parses --cidfile out of its argv and,
    when $WRITE_CID=1, writes a fixed fake container id there before sleeping
    (simulating a long-running foreground container); `stop [-t <n>] <id>`
    extracts the id as its last positional arg (robust to the `-t <n>`
    grace-period prefix), appends it to $STOP_LOG and exits 0 (simulating a
    successful stop) -- unless $STOP_NO_CONTAINER=1, in which case it prints
    "No such container" to stderr and exits 1 instead (simulating a `--rm`
    container that already self-removed before our stop landed).
    """
    script = bin_dir / name
    script.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "run" ]; then\n'
        "  shift\n"
        '  cidfile=""\n'
        "  while [ $# -gt 0 ]; do\n"
        '    if [ "$1" = "--cidfile" ]; then cidfile="$2"; fi\n'
        "    shift\n"
        "  done\n"
        '  if [ -n "$cidfile" ] && [ "$WRITE_CID" = "1" ]; then\n'
        f"    printf '%s' {_FAKE_CID!r} > \"$cidfile\"\n"
        "  fi\n"
        "  sleep 60\n"
        'elif [ "$1" = "stop" ]; then\n'
        '  cid="${@: -1}"\n'
        '  if [ "$STOP_NO_CONTAINER" = "1" ]; then\n'
        "    printf 'Error: No such container: %s\\n' \"$cid\" >&2\n"
        "    exit 1\n"
        "  fi\n"
        '  printf \'%s\\n\' "$cid" >> "$STOP_LOG"\n'
        "  exit 0\n"
        "fi\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@pytest.mark.serial
@pytest.mark.timeout(60)
def test_container_stop_should_report_success_when_container_already_removed(tmp_path, monkeypatch):
    """A `--rm` container can self-remove before our best-effort `stop` runs
    against it -- the runtime then reports "no such container" rather than
    stopping anything. That is not a failure to warn loudly about: the
    container is already gone, so the verdict must be a success, not a false
    "stop failed"."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "docker")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("WRITE_CID", "1")
    monkeypatch.setenv("STOP_NO_CONTAINER", "1")  # simulate --rm self-removal before our stop
    stop_log = tmp_path / "stop.log"
    monkeypatch.setenv("STOP_LOG", str(stop_log))

    calls: list[tuple[str, str | None, bool | None]] = []
    result = run(
        command=["docker", "run", "--rm", "some-agent-image"],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        work_dir=tmp_path,
        log_path=tmp_path / "round.log",
        env_extra={},
        on_container_orphan_risk=lambda *a: calls.append(a),
    )

    assert result.timed_out is True
    assert calls == [("docker", _FAKE_CID, True)]  # already-gone → success, NOT a false failure
    assert not stop_log.exists()  # pins the exit-1 (already-gone) path, not the exit-0 success path


@pytest.mark.serial
@pytest.mark.timeout(60)
def test_container_stop_should_not_run_stop_when_cidfile_content_is_not_a_hex_id(
    tmp_path, monkeypatch
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "docker")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    stop_log = tmp_path / "stop.log"
    monkeypatch.setenv("STOP_LOG", str(stop_log))
    cidfile = tmp_path / "poison.cid"
    cidfile.write_text("--all", encoding="utf-8")

    cid, stop_ok = agent_runtime._best_effort_container_stop("docker", cidfile)

    assert (cid, stop_ok) == (None, None)
    assert not stop_log.exists()


@pytest.mark.serial
@pytest.mark.timeout(60)
def test_container_stop_should_not_read_an_oversize_cidfile_whole(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "docker")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("STOP_LOG", str(tmp_path / "stop.log"))
    cidfile = tmp_path / "big.cid"
    cidfile.write_bytes(b"a" * (5 * 1024 * 1024))

    cid, stop_ok = agent_runtime._best_effort_container_stop("docker", cidfile)

    assert (cid, stop_ok) == (None, None)


@pytest.mark.serial  # real-subprocess timing (0.2.19 lesson): the -n auto
# gate's own CPU contention can stretch the fake runtime's fork+exec past a
# tight window, racing the R1128 kill against the cidfile write below — run
# this pass with no competing xdist workers instead of over-widening timeout_s.
@pytest.mark.timeout(60)
def test_container_run_command_should_terminate_loudly_with_best_effort_stop_when_round_times_out(
    tmp_path, monkeypatch
):
    """A `docker run` command that's still alive at the R1128 wall-clock hits
    the timeout-kill path: the injected --cidfile is read back, `docker stop
    <id>` is actually invoked (proven via the fake stub's own STOP_LOG), and
    the caller's on_container_orphan_risk callback fires with the recovered
    id + a successful stop. Review fix 2: our OWN injected cidfile is cleaned
    up once the round is done -- it must not linger as a leaked tmp file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "docker")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("WRITE_CID", "1")
    stop_log = tmp_path / "stop.log"
    monkeypatch.setenv("STOP_LOG", str(stop_log))
    log_path = tmp_path / "round.log"

    calls: list[tuple[str, str | None, bool | None]] = []
    result = run(
        command=["docker", "run", "--rm", "some-agent-image"],
        prompt_arg_template=[],
        prompt="x",
        # Generous headroom (0.2.19 flake lesson): a cold-cache fork+exec of a
        # freshly chmod'd script can occasionally take noticeably longer than a
        # warm rerun -- 5s gives the fake runtime's cidfile write real margin
        # to land before the R1128 kill fires (also serial-marked above).
        timeout_s=5,
        work_dir=tmp_path,
        log_path=log_path,
        env_extra={},
        on_container_orphan_risk=lambda *a: calls.append(a),
    )

    assert result.timed_out is True
    assert not _alive(result.pid), "docker run launcher must be reaped like any other agent"
    assert calls == [("docker", _FAKE_CID, True)]
    assert stop_log.read_text(encoding="utf-8").strip() == _FAKE_CID
    assert not (tmp_path / (log_path.name + ".cid")).exists(), (
        "our own injected cidfile must be cleaned up once the round is done"
    )


@pytest.mark.serial  # real-subprocess timing (0.2.19 lesson): the -n auto
# gate's own CPU contention can stretch the fake runtime's fork+exec past a
# tight window, racing the R1128 kill against the cidfile write below — run
# this pass with no competing xdist workers instead of over-widening timeout_s.
@pytest.mark.timeout(60)
def test_podman_run_command_should_terminate_loudly_with_best_effort_stop_when_round_times_out(
    tmp_path, monkeypatch
):
    """Same full inject/stop/cleanup flow as the docker-flavored sibling
    above, but through the podman binary name -- `_detect_container_run`
    reports the runtime by its actual basename, and the fake stub, cidfile
    injection, stop invocation and callback all key off that name too."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "podman")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("WRITE_CID", "1")
    stop_log = tmp_path / "stop.log"
    monkeypatch.setenv("STOP_LOG", str(stop_log))
    log_path = tmp_path / "round.log"

    calls: list[tuple[str, str | None, bool | None]] = []
    result = run(
        command=["podman", "run", "--rm", "some-agent-image"],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        work_dir=tmp_path,
        log_path=log_path,
        env_extra={},
        on_container_orphan_risk=lambda *a: calls.append(a),
    )

    assert result.timed_out is True
    assert not _alive(result.pid), "podman run launcher must be reaped like any other agent"
    assert calls == [("podman", _FAKE_CID, True)]
    assert stop_log.read_text(encoding="utf-8").strip() == _FAKE_CID
    assert not (tmp_path / (log_path.name + ".cid")).exists(), (
        "our own injected cidfile must be cleaned up once the round is done"
    )


@pytest.mark.serial  # real-subprocess timing (0.2.19 lesson): see the other
# serial-marked tests in this file for the full rationale.
@pytest.mark.timeout(60)
def test_operator_provided_cidfile_should_be_read_but_not_deleted_when_round_times_out(
    tmp_path, monkeypatch
):
    """Review fix 2's other half: run() only ever deletes a cidfile IT
    injected. An operator who already passes their own --cidfile gets it
    read back for the best-effort stop, same as any injected one -- but it
    must never be deleted by run()'s cleanup; that file is the operator's."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "docker")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("WRITE_CID", "1")
    stop_log = tmp_path / "stop.log"
    monkeypatch.setenv("STOP_LOG", str(stop_log))
    operator_cidfile = tmp_path / "operator.cid"

    calls: list[tuple[str, str | None, bool | None]] = []
    result = run(
        command=["docker", "run", "--cidfile", str(operator_cidfile), "--rm", "some-agent-image"],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        work_dir=tmp_path,
        log_path=tmp_path / "round.log",
        env_extra={},
        on_container_orphan_risk=lambda *a: calls.append(a),
    )

    assert result.timed_out is True
    assert calls == [("docker", _FAKE_CID, True)]
    assert operator_cidfile.exists(), "an operator-supplied --cidfile must survive run()'s cleanup"
    assert operator_cidfile.read_text(encoding="utf-8").strip() == _FAKE_CID


@pytest.mark.serial  # real-subprocess timing (0.2.19 lesson): the -n auto
# gate's own CPU contention can stretch the fake runtime's fork+exec past a
# tight window, racing the R1128 kill against the cidfile write below — run
# this pass with no competing xdist workers instead of over-widening timeout_s.
@pytest.mark.timeout(60)
def test_container_run_command_should_only_warn_when_no_id_is_recoverable(tmp_path, monkeypatch):
    """The container never actually started (WRITE_CID unset -> the cidfile
    stays empty): no `stop` is attempted, but the callback still fires — the
    honest floor is the loud report, not a fabricated stop."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "docker")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.delenv("WRITE_CID", raising=False)
    stop_log = tmp_path / "stop.log"
    monkeypatch.setenv("STOP_LOG", str(stop_log))

    calls: list[tuple[str, str | None, bool | None]] = []
    result = run(
        command=["docker", "run", "--rm", "some-agent-image"],
        prompt_arg_template=[],
        prompt="x",
        # Generous headroom (0.2.19 flake lesson): a cold-cache fork+exec of a
        # freshly chmod'd script can occasionally take noticeably longer than a
        # warm rerun -- 5s gives the fake runtime's cidfile write real margin
        # to land before the R1128 kill fires (also serial-marked above).
        timeout_s=5,
        work_dir=tmp_path,
        log_path=tmp_path / "round.log",
        env_extra={},
        on_container_orphan_risk=lambda *a: calls.append(a),
    )

    assert result.timed_out is True
    assert calls == [("docker", None, None)]
    assert not stop_log.exists(), "no id recoverable -> stop must never be attempted"


@pytest.mark.serial  # real-subprocess timing (0.2.19 lesson): the -n auto
# gate's own CPU contention can stretch the fake runtime's fork+exec past a
# tight window, racing the R1128 kill against the assertions below — run
# this pass with no competing xdist workers instead of over-widening timeout_s.
@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    "build_command",
    [
        lambda docker_path: [docker_path, "-H", "unix:///var/run/docker.sock", "run", "image"],
        lambda docker_path: ["env", "X=1", docker_path, "run", "image"],
    ],
    ids=["global-flag-form", "env-wrapped-form"],
)
def test_bypass_forms_should_warn_without_stop_attempt_when_terminated(
    tmp_path, monkeypatch, build_command
):
    """Review fix 1 — the global-flag and `env`-wrapped bypass forms (2 of
    the 4 named forms; `sudo docker run ...`/`sudo -u ... docker run ...`
    are covered at the pure-detector level in
    test_detect_container_run_should_recognize_run_commands and
    test_detect_container_run_index_should_be_true_only_for_unwrapped_unflagged_form
    -- spawning a real `sudo` here would need passwordless sudo, which isn't
    a safe test-environment assumption): DETECTED (broad check), so the loud
    warn + round_container_orphan_risk event still fires on termination --
    but being outside the CONSERVATIVE injection gate (run_idx != 1), no
    --cidfile is ever touched and no stop is attempted: the callback reports
    (runtime, None, None), same shape as "id not recoverable"."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_script = bin_dir / "docker"
    docker_script.write_text(
        '#!/bin/bash\nif [ "$1" = "stop" ]; then exit 0; fi\nsleep 60\n',
        encoding="utf-8",
    )
    docker_script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    calls: list[tuple[str, str | None, bool | None]] = []
    log_path = tmp_path / "round.log"
    result = run(
        command=build_command(str(docker_script)),
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        work_dir=tmp_path,
        log_path=log_path,
        env_extra={},
        on_container_orphan_risk=lambda *a: calls.append(a),
    )

    assert result.timed_out is True
    assert calls == [("docker", None, None)]
    assert not (tmp_path / (log_path.name + ".cid")).exists(), (
        "bypass forms are outside the conservative gate -- no cidfile is ever created"
    )


@pytest.mark.serial
@pytest.mark.timeout(60)
def test_container_run_should_inject_cidfile_outside_log_dir_and_work_dir(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "docker")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("WRITE_CID", "1")
    monkeypatch.setenv("STOP_LOG", str(tmp_path / "stop.log"))
    captured: list[str] = []
    real_inject = agent_runtime._inject_cidfile

    def _spy_inject(command, run_idx, path):
        captured.append(str(path))
        return real_inject(command, run_idx, path)

    monkeypatch.setattr(agent_runtime, "_inject_cidfile", _spy_inject)

    run(
        command=["docker", "run", "--rm", "img"],
        prompt_arg_template=[],
        prompt="x",
        timeout_s=5,
        work_dir=tmp_path,
        log_path=tmp_path / "round.log",
        env_extra={},
        on_container_orphan_risk=lambda *a: None,
    )

    assert captured, "cidfile was never injected"
    cid_path = Path(captured[0])
    assert not cid_path.is_relative_to(tmp_path)  # outside work_dir/log_dir


def test_non_container_command_should_leave_argv_and_callback_unchanged(tmp_path, monkeypatch):
    """A normal (non-container) command's spawn path is byte-unchanged: no
    --cidfile is injected, no .cid file appears, and on_container_orphan_risk
    is never called even though the callback is wired up."""
    monkeypatch.delenv("WRITE_CID", raising=False)
    argv_dump = tmp_path / "argv.txt"
    script = tmp_path / "fake-agent.sh"
    script.write_text(
        f'#!/bin/bash\nprintf \'%s\\n\' "$@" > "{argv_dump}"\nexit 0\n',
        encoding="utf-8",
    )
    script.chmod(0o755)

    calls: list[tuple] = []
    log_path = tmp_path / "round.log"
    result = run(
        command=[str(script), "--flag", "value"],
        prompt_arg_template=["--prompt", "{prompt}"],
        prompt="hello",
        timeout_s=30,
        work_dir=tmp_path,
        log_path=log_path,
        env_extra={},
        on_container_orphan_risk=lambda *a: calls.append(a),
    )

    assert result.exit_code == 0
    assert argv_dump.read_text(encoding="utf-8").splitlines() == [
        "--flag",
        "value",
        "--prompt",
        "hello",
    ]
    assert calls == []
    assert not (tmp_path / (log_path.name + ".cid")).exists()
