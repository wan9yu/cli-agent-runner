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

from agent_runner.agent_runtime import _is_container_run_command, run


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
    ],
)
def test_is_container_run_command(command: list[str], expected: bool) -> None:
    assert _is_container_run_command(command) is expected


def _write_fake_runtime(bin_dir: Path, name: str) -> Path:
    """A fake docker/podman stub: `run` parses --cidfile out of its argv and,
    when $WRITE_CID=1, writes a fixed fake container id there before sleeping
    (simulating a long-running foreground container); `stop <id>` appends the
    id to $STOP_LOG and exits 0 (simulating a successful container stop).
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
        '    printf \'%s\' "fakecid123" > "$cidfile"\n'
        "  fi\n"
        "  sleep 60\n"
        'elif [ "$1" = "stop" ]; then\n'
        '  printf \'%s\\n\' "$2" >> "$STOP_LOG"\n'
        "  exit 0\n"
        "fi\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@pytest.mark.serial  # real-subprocess timing (0.2.19 lesson): the -n auto
# gate's own CPU contention can stretch the fake runtime's fork+exec past a
# tight window, racing the R1128 kill against the cidfile write below — run
# this pass with no competing xdist workers instead of over-widening timeout_s.
@pytest.mark.timeout(60)
def test_container_run_command_terminates_loudly_and_best_effort_stops(tmp_path, monkeypatch):
    """A `docker run` command that's still alive at the R1128 wall-clock hits
    the timeout-kill path: the injected --cidfile is read back, `docker stop
    <id>` is actually invoked (proven via the fake stub's own STOP_LOG), and
    the caller's on_container_orphan_risk callback fires with the recovered
    id + a successful stop."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_runtime(bin_dir, "docker")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("WRITE_CID", "1")
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
    assert not _alive(result.pid), "docker run launcher must be reaped like any other agent"
    assert calls == [("docker", "fakecid123", True)]
    assert stop_log.read_text(encoding="utf-8").strip() == "fakecid123"


@pytest.mark.serial  # real-subprocess timing (0.2.19 lesson): the -n auto
# gate's own CPU contention can stretch the fake runtime's fork+exec past a
# tight window, racing the R1128 kill against the cidfile write below — run
# this pass with no competing xdist workers instead of over-widening timeout_s.
@pytest.mark.timeout(60)
def test_container_run_command_with_no_recoverable_id_only_warns(tmp_path, monkeypatch):
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


def test_non_container_command_argv_and_callback_unchanged(tmp_path, monkeypatch):
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
