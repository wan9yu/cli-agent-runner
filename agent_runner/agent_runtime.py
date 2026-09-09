"""Agent subprocess management — spawns the configured agent CLI process.

Defenses encoded here:
- R1128: ROUND_TIMEOUT is wall-clock hard wall (no activity-based extension)
- #307: start_new_session=True isolates subprocess in its own pgrp
- env injection: per-CLI envs come from AgentConfig.env (preset-supplied);
  no implicit injection in this module.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess  # noqa: TID251 — sanctioned subprocess caller
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

from agent_runner.api_types import _round_ok
from agent_runner.clock import SYSTEM_CLOCK, Clock

REAP_GRACE_S = 5

# Backstop cap on the stored child "name" (see _live_children). The primary
# source (kernel comm) is already bounded (15 bytes on Linux); this only
# guards the argv[0]-basename fallback used when comm is unavailable.
_MAX_CHILD_NAME_LEN = 64


def signal_name(exit_code: int) -> str | None:
    """Signal name for a signal death, else None.

    Handles both the Python-negative form (``-15``) and the shell ``128+N``
    form (``143``). Returns None for a non-signal exit — including a legitimate
    ``> 128`` code (e.g. 200) that is not a valid signal number.
    """
    if exit_code < 0:
        n = -exit_code
    elif exit_code > 128:
        n = exit_code - 128
    else:
        return None
    try:
        return signal.Signals(n).name
    except ValueError:
        return None


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    duration_s: float
    timed_out: bool
    pid: int
    killed_for_grace: bool = False

    @property
    def ok(self) -> bool:
        return _round_ok(self.exit_code, self.timed_out)


def _build_argv(command: list[str], prompt_arg_template: list[str], prompt: str) -> list[str]:
    """Build full argv: command + prompt args (with {prompt} substituted)."""
    return list(command) + [a.replace("{prompt}", prompt) for a in prompt_arg_template]


def _kill_stray_descendants(descendants: list[dict]) -> None:
    """Hard-kill each descendant's OWN process group. Covers a descendant
    that ``setsid()``'d off the leader's pgroup (POSIX ``setsid()`` changes
    pgid+sid but NOT ppid), so it sits outside whatever pgroup the caller
    just SIGKILLed and is otherwise left running -- orphaned, not reaped.
    ``descendants`` is an earlier ``_live_children(proc)`` snapshot (see the
    callers: it MUST be taken before the leader can die, because once it
    does, a detached descendant is reparented to init and is no longer
    reachable by walking down from the leader's — by then vacated, possibly
    reused — pid). Each entry may already be dead by now (fine, swallowed)
    or may share the leader's own pgid (already SIGKILLed by the caller —
    redundant, harmless).

    Defense-in-depth self-guard: never signal agent-runner's OWN process
    group, even though ``_live_children`` is rooted at the round child (a
    strict downward walk from ITS pid) and so cannot structurally reach
    upward to the supervisor."""
    own_pgid = os.getpgrp()
    for entry in descendants:
        pid = entry["pid"]
        try:
            dpgid = os.getpgid(pid)
        except OSError:
            continue  # already gone
        if dpgid == own_pgid:
            continue  # never signal agent-runner's own group
        try:
            os.killpg(dpgid, signal.SIGKILL)
        except OSError:
            pass  # already gone, or the group leader raced us to exit


def _kill_pgroup(proc: subprocess.Popen, clock: Clock = SYSTEM_CLOCK) -> None:
    """SIGTERM the pgroup, grace, then SIGKILL — the reap primitive shared by
    the round-timeout path and ``run``'s BaseException handler (which fires on
    a SIGTERM landing while the round is unwinding from a first one).
    ``round_cmd``'s SIGTERM handler stays installed for the whole process life
    (it converts every SIGTERM into a fresh ``KeyboardInterrupt``, not just the
    first), so a re-entrant SIGTERM during the grace sleep below raises HERE —
    shielded (caught and retried) so the SIGKILL escalation always runs. An
    operator's impatient double-kill must not leave the agent outliving the
    grace period unreaped.

    Also reaps any live descendant that detached to its own process group
    (see ``_kill_stray_descendants``) — the pgroup SIGKILL above only ever
    reaches ``pgid``, never a ``setsid()``'d-off descendant. The snapshot is
    taken up front, before the SIGTERM, while the leader (and hence its
    process subtree) is still guaranteed resolvable."""
    pgid = proc.pid
    stray, _ignored = _live_children(proc)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        pass
    deadline = (
        clock.monotonic() + REAP_GRACE_S
    )  # monotonic: an NTP step must not stretch/skip the reap
    while clock.monotonic() < deadline and proc.poll() is None:
        try:
            clock.sleep(0.1)
        except KeyboardInterrupt:
            pass  # shielded: keep waiting out the grace window, never skip SIGKILL
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass
    _kill_stray_descendants(stray)
    while True:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass  # reaped via the SIGKILL above regardless; nothing more to wait for
        except KeyboardInterrupt:
            continue  # shielded: still must reap before returning
        return


def _live_children(
    proc: subprocess.Popen,
    *,
    ignore_patterns: list[re.Pattern[str]] | None = None,
    max_n: int = 5,
) -> tuple[list[dict], list[dict]]:
    """Live (non-zombie) descendants of ``proc``, split into ``(live, ignored)``.

    Each entry is ``{"name": <process name>, "pid": <int>}``; an ignored entry
    also carries ``"matched": <pattern str>``. We store only a bounded name +
    pid, NOT argv — process arguments are where secrets leak (PGPASSWORD=…,
    --api-key …, redis://:pass@…) and these lists are persisted to
    events-*.jsonl. Ignore-pattern MATCHING runs against the full cmdline
    (detection unchanged); only what we STORE is minimized.

    The stored name is read from the kernel-reported process name (comm —
    e.g. /proc/<pid>/comm on Linux), not from ``Path(argv[0]).name``: comm is
    derived from the exec()'d binary, not from argv[0], and is itself
    kernel-bounded (15 bytes on Linux) -- so a process that rewrites its own
    argv[0] to an arbitrary, slash-free string (``exec -a <secret>``,
    setproctitle, …) can't smuggle that string into the stored name the way
    a plain ``Path(argv[0]).name`` (a no-op on a slash-free string) would.
    The argv[0]-basename is kept only as a fallback for when comm is
    unavailable, length-capped as a backstop against the same rewrite class.
    """
    try:
        parent = psutil.Process(proc.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return [], []
    live: list[dict] = []
    ignored: list[dict] = []
    for child in parent.children(recursive=True):
        try:
            if child.status() == psutil.STATUS_ZOMBIE:
                continue
            argv = child.cmdline()
            full = " ".join(argv) or child.name()  # MATCHING only
            fallback = (Path(argv[0]).name if argv else "")[:_MAX_CHILD_NAME_LEN]
            name = child.name() or fallback
            pid = child.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        matched = None
        if ignore_patterns:
            for p in ignore_patterns:
                if p.search(full):
                    matched = p.pattern
                    break
        if matched is not None:
            if len(ignored) < max_n:
                ignored.append({"name": name, "pid": pid, "matched": matched})
        elif len(live) < max_n:
            live.append({"name": name, "pid": pid})
        if len(live) >= max_n and len(ignored) >= max_n:
            break
    return live, ignored


# Exact compact bytes — matches claude CLI's no-whitespace JSONL output.
# A future CLI variant emitting `{"type": "result", ...}` (with space) would
# bypass this scan; revisit if that happens.
_RESULT_MARKER = b'"type":"result"'

# Decoupled from the 0.2s poll tick: the marker scan only needs to notice the
# result within max_grace_after_result_s (seconds, integer), so scanning more
# often than this buys nothing but re-read cost on a growing round log.
_RESULT_SCAN_INTERVAL_S = 1.0


def resolve_exec_target(command0: str, work_dir: Path, env_path: str | None = None) -> str | None:
    """Model of Popen's POSIX exec resolution for argv[0].

    Kept beside the ``Popen`` in :func:`run` so validation and exec cannot
    drift: a slash-containing argv[0] resolves against the child's cwd
    (``work_dir``); a bare name is looked up on the CHILD's PATH
    (``env_path`` — pass ``[agent.env]``'s PATH override if set; ``None``
    falls back to the supervisor's, matching env inheritance). Returns the
    resolved executable path, or ``None`` if it would not exec.
    """
    if "/" in command0:
        candidate = Path(command0)
        if not candidate.is_absolute():
            candidate = work_dir / candidate
        return shutil.which(str(candidate))
    return shutil.which(command0, path=env_path)


# Container-orphan defense: a configured `docker run` / `podman run` command's
# conmon/containerd process double-fork-detaches out of the pgroup
# `_kill_pgroup` signals, so a killpg-based SIGKILL can silently leave the
# CONTAINER itself running -- defeating the R1128 hard-wall with no visible
# failure. Detection below is deliberately BROAD / safety-biased (unwraps a
# leading `sudo`/`env` wrapper, skips recognized docker/podman global flags
# before `run`) -- a false positive there just prints an extra warning for a
# coincidentally similar command, which is far cheaper than a silent orphan.
# The separate `--cidfile` injection + best-effort `stop` stays CONSERVATIVE
# (only the unwrapped, unflagged `docker run ...` / `podman run ...` shape)
# since that path actually touches the spawned argv -- see run()'s own note.
# Full container lifecycle management (cgroup delegation, a containment
# ladder) is out of scope here -- left for a future release.
_CONTAINER_RUNTIMES = frozenset({"docker", "podman"})

# docker/podman GLOBAL flags (before the subcommand) that take a separate
# value token -- skipped in pairs so `docker -H unix:///var/run/docker.sock
# run ...` / `docker --context foo run ...` still resolve to `run`. Not
# exhaustive of every global flag either CLI supports; an unrecognized
# `--foo value` global flag would mis-consume only the flag itself (its value
# token then fails the "run" check on the next iteration, same as an
# unrecognized subcommand -- detection just returns None, never a false
# "run"). Safety-biased detection tolerates that; the conservative injection
# path never uses this table at all (see _detect_container_run's docstring).
_DOCKER_GLOBAL_FLAGS_WITH_VALUE = frozenset(
    {
        "-H",
        "--host",
        "--context",
        "-c",
        "--config",
        "-l",
        "--log-level",
        "--tlscacert",
        "--tlscert",
        "--tlskey",
    }
)


def _unwrap_command_prefix(command: list[str]) -> list[str]:
    """Strip a leading `sudo` (its own flags, including `-u`/`--user <who>`)
    and/or `env` (its own flags + leading `VAR=val` assignments), returning
    the remainder starting at the REAL binary token. Best-effort, not a shell
    parser -- covers the realistic wrapper shapes this defense targets
    (`sudo docker run ...`, `env X=1 docker run ...`, `sudo env X=1 docker
    run ...`), not arbitrary wrapper chains. Returns `command` unchanged when
    neither wrapper is present."""
    i = 0
    n = len(command)
    if i < n and Path(command[i]).name == "sudo":
        i += 1
        while i < n and command[i].startswith("-"):
            if command[i] in ("-u", "--user") and i + 1 < n:
                i += 2
            else:
                i += 1
    if i < n and Path(command[i]).name == "env":
        i += 1
        while i < n and (command[i].startswith("-") or "=" in command[i]):
            i += 1
    return command[i:]


def _detect_container_run(command: list[str]) -> tuple[str, int] | None:
    """``(runtime_basename, run_index)`` when `command` -- after unwrapping a
    leading `sudo`/`env` wrapper and skipping recognized docker/podman global
    flags -- resolves to a docker/podman `run` invocation; else None.
    `run_index` is the index of the `run` token WITHIN THE ORIGINAL
    `command` (not the unwrapped slice), so callers can address `command`
    directly. Deliberately broad -- drives only the loud warn +
    `round_container_orphan_risk` event; see the module note above for why
    the separate injection path stays conservative instead of reusing this."""
    real = _unwrap_command_prefix(command)
    if not real or Path(real[0]).name not in _CONTAINER_RUNTIMES:
        return None
    offset = len(command) - len(real)
    runtime = Path(real[0]).name
    i = 1
    while i < len(real):
        tok = real[i]
        if tok == "run":
            return runtime, offset + i
        if not tok.startswith("-"):
            return None  # a non-flag, non-`run` subcommand (build/ps/... )
        if tok in _DOCKER_GLOBAL_FLAGS_WITH_VALUE and i + 1 < len(real):
            i += 2
        else:
            i += 1
    return None


# `docker run`/`podman run` OPTIONS known to take a separate value token --
# used only to walk PAST them without mistaking their value for IMAGE (e.g.
# `--name foo`: without this, a naive scan would stop at "foo" thinking it's
# IMAGE). Deliberately NOT the inverse (assume-value-unless-next-looks-like-
# a-flag): that heuristic mis-consumed a bare boolean flag's OWN following
# token (e.g. `--rm image` -- `--rm` takes no value, but "eats" "image" as if
# it did) and walked straight past the real IMAGE boundary into the
# container's own trailing args. An unrecognized value-taking flag not in
# this table degrades safely: the scan just stops one token early (treating
# its value as IMAGE), at worst missing an operator's --cidfile that comes
# after it -- not a full docker-run option parser, an advisory best-effort
# walk.
_DOCKER_RUN_FLAGS_WITH_VALUE = frozenset(
    {
        "-e", "--env", "--env-file",
        "-v", "--volume", "--volumes-from", "--mount",
        "-p", "--publish",
        "--name",
        "-w", "--workdir",
        "-u", "--user",
        "-m", "--memory", "--memory-swap", "--memory-reservation",
        "--cpus", "--cpu-shares", "--cpuset-cpus", "--cpuset-mems",
        "--network", "--net", "--ip", "--ip6", "--mac-address", "--add-host",
        "-l", "--label", "--label-file",
        "--restart",
        "--entrypoint",
        "-h", "--hostname",
        "--dns", "--dns-search", "--dns-option",
        "--link",
        "--log-driver", "--log-opt",
        "--pid", "--ipc", "--uts", "--userns",
        "--security-opt",
        "--stop-signal", "--stop-timeout",
        "--device", "--device-cgroup-rule",
        "--cap-add", "--cap-drop",
        "--tmpfs",
        "--ulimit",
        "--shm-size",
        "--health-cmd", "--health-interval", "--health-retries", "--health-timeout",
        "--health-start-period",
        "--platform",
        "--pull",
        "--pids-limit",
        "--blkio-weight",
        "--group-add",
        "--isolation",
        "--runtime",
        "--gpus",
        "-a", "--attach",
        "--expose",
        "--cidfile",
    }
)  # fmt: skip


def _cidfile_flag_value(command: list[str], run_idx: int) -> str | None:
    """Value of an operator-supplied `--cidfile <path>` / `--cidfile=path`
    within the docker/podman OPTIONS block -- `command[run_idx + 1 :]`, up to
    (not including) the first bare token, which is the IMAGE argument where
    docker's own options end. Never scans past IMAGE into the container's
    OWN args, which may coincidentally carry a `--cidfile`-looking token
    meant for the containerized program, not docker/podman itself."""
    i = run_idx + 1
    n = len(command)
    while i < n:
        tok = command[i]
        if tok == "--cidfile" and i + 1 < n:
            return command[i + 1]
        if tok.startswith("--cidfile="):
            return tok.split("=", 1)[1]
        if not tok.startswith("-"):
            break  # IMAGE reached -- the OPTIONS block ends here
        flag = tok.split("=", 1)[0]
        if flag in _DOCKER_RUN_FLAGS_WITH_VALUE and "=" not in tok and i + 1 < n:
            i += 2
        else:
            i += 1
    return None


def _command_has_cidfile_flag(command: list[str], run_idx: int) -> bool:
    """Whether ANY `--cidfile` / `--cidfile=…` token appears after the `run`
    subcommand, scanned POSITION-INDEPENDENTLY (every token in
    ``command[run_idx + 1 :]``, not stopping at the IMAGE boundary that
    ``_cidfile_flag_value`` guesses from ``_DOCKER_RUN_FLAGS_WITH_VALUE``).

    ``_cidfile_flag_value`` can MISS an operator's `--cidfile` when it sits past
    a value-taking flag NOT in that table -- its walk stops one token early,
    mistaking the untabled flag's value for IMAGE. Gating injection on this
    blind-spot-free presence check means a second `--cidfile` is never appended
    when one is already present, so ``run()`` can't emit a malformed
    ``docker run --cidfile OURS … --cidfile OPERATOR image``. (A `--cidfile`
    belonging to the CONTAINERIZED program, past IMAGE, would also suppress
    injection: acceptable -- the only cost is losing the best-effort stop for
    that contrived config, and detection's warn + ``round_container_orphan_risk``
    event already guarantee no silent orphan.)"""
    return any(tok == "--cidfile" or tok.startswith("--cidfile=") for tok in command[run_idx + 1 :])


def _inject_cidfile(command: list[str], run_idx: int, cidfile_path: Path) -> list[str]:
    """Insert `--cidfile <cidfile_path>` right after the `run` subcommand at
    `run_idx` -- ahead of any IMAGE argument, which must always follow the
    docker/podman OPTIONS block -- so the container's own id is recoverable
    for a best-effort `stop` at kill time. Only called on the CONSERVATIVE
    injection path (`run_idx == 1`: no wrapper, no global flags before
    `run`) when `command` carries no `--cidfile` of its own."""
    return command[: run_idx + 1] + ["--cidfile", str(cidfile_path)] + command[run_idx + 1 :]


def _best_effort_container_stop(
    runtime: str, cidfile: Path | None
) -> tuple[str | None, bool | None]:
    """Best-effort ``<runtime> stop <id>`` using the container id the runtime
    itself wrote into `cidfile` at spawn time. Returns
    ``(container_id, stop_ok)``: `container_id` is None when no id could be
    recovered (the container never actually started, or the cidfile is still
    empty) -- `stop_ok` is then also None, meaning no stop was attempted.
    Otherwise `stop_ok` is whether the `stop` subprocess exited zero. Every
    failure here is swallowed -- this is advisory best-effort, never a
    guarantee; the caller's loud warning + event is the actual floor when
    this doesn't land."""
    if cidfile is None:
        return None, None
    try:
        cid = cidfile.read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if not cid:
        return None, None
    try:
        result = subprocess.run(
            [runtime, "stop", cid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return cid, result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return cid, False


def _terminate_agent(
    proc: subprocess.Popen,
    clock: Clock,
    *,
    container_runtime: str | None,
    container_cidfile: Path | None,
    on_container_orphan_risk: Callable[[str, str | None, bool | None], None] | None,
) -> None:
    """Reap the agent pgroup (``_kill_pgroup``), then -- for a
    container-launching command only -- make a best-effort container `stop`
    and report the outcome via `on_container_orphan_risk`. The single
    hard-kill entry point ``run()`` uses for all three of its termination
    paths (R1128 wall-clock, grace-kill, and the BaseException reap), so the
    container handling isn't duplicated three times."""
    _kill_pgroup(proc, clock)
    if container_runtime is not None:
        cid, stop_ok = _best_effort_container_stop(container_runtime, container_cidfile)
        if on_container_orphan_risk is not None:
            on_container_orphan_risk(container_runtime, cid, stop_ok)


def run(
    *,
    command: list[str],
    prompt_arg_template: list[str],
    prompt: str,
    prompt_delivery: str = "argv",
    timeout_s: int,
    work_dir: Path,
    log_path: Path,
    env_extra: dict[str, str],
    max_grace_after_result_s: int = 0,
    progress_callback: Callable[[dict], None] | None = None,
    progress_interval_s: int = 0,
    on_grace_extended: Callable[[list[dict], list[dict]], None] | None = None,
    grace_kill_ignore_patterns: list[re.Pattern[str]] | None = None,
    on_container_orphan_risk: Callable[[str, str | None, bool | None], None] | None = None,
    clock: Clock = SYSTEM_CLOCK,
) -> RunResult:
    """Spawn the agent subprocess and wait for exit or timeout.

    Wall-clock timeout (R1128). On timeout: SIGTERM pgroup → REAP_GRACE_S → SIGKILL.

    work_dir: the agent child's working directory; callers pass the
    already-absolute cfg.runtime.work_dir. CLIs with no --cwd flag of their
    own (e.g. pi) depend on this.

    max_grace_after_result_s: when > 0, start a countdown after the first
    type=result event is detected in the log. After it elapses, reap the
    process group only if the agent has no live worker processes left (a
    genuine hang). If a worker is still running (e.g. a backgrounded build),
    do not reap — invoke ``on_grace_extended`` once and keep waiting until the
    round finishes or hits the wall-clock ``timeout_s`` ceiling. 0 = disabled.

    progress_callback: when not None and progress_interval_s > 0, called every
    progress_interval_s seconds with a dict of log stats (log_size_kb,
    last_write_age_s, wall_age_s). Keeps agent_runtime event-free; callers
    build the callback to emit events.

    grace_kill_ignore_patterns: pre-compiled regex patterns; child cmdlines
    matching any pattern (re.search) are excluded from the liveness count
    (persistent helpers that aren't real workers). None = no filtering.

    on_container_orphan_risk: when `command` is recognized as a `docker run` /
    `podman run` invocation -- including `sudo`/`env`-wrapped and
    global-flagged forms; see ``_detect_container_run`` -- called exactly
    once IF this round is actually terminated by ``run()`` (R1128 timeout,
    grace-kill, or the BaseException reap) -- never on a round that exits on
    its own. Args are ``(runtime, container_id_or_None, stop_ok_or_None)``:
    killpg-based termination reaches the launcher process, not the container
    itself, so this is the caller's signal to warn loudly + record the
    reduced guarantee. `container_id`/`stop_ok` stay None for a detected
    form the CONSERVATIVE `--cidfile` injection doesn't cover (see
    ``_detect_container_run``'s note) -- the warn/event still fires. None =
    no callback (container orphan risk still gets a best-effort ``stop``
    attempt where covered; it just isn't reported).
    """
    stdin_mode = prompt_delivery == "stdin"
    # Container-orphan defense: detect BEFORE building argv (spawn_command may
    # gain an injected --cidfile) -- see _detect_container_run's module-level
    # note. A non-container command's argv is byte-identical to before:
    # spawn_command stays `command` and nothing else here changes.
    spawn_command = command
    container_runtime: str | None = None
    container_cidfile: Path | None = None
    container_cidfile_is_ours = False
    detected = _detect_container_run(command)
    if detected is not None:
        container_runtime, run_idx = detected
        # CONSERVATIVE injection: only the unwrapped, unflagged shape
        # (command[1] == "run" literally -- no sudo/env wrapper, no global
        # flags before `run`) gets a --cidfile touched. A wrapped or
        # global-flagged form still gets the loud warn + event above (broad
        # detection fired), it just never gets an injected cidfile or a
        # stop attempt -- on_container_orphan_risk reports (runtime, None,
        # None) for those, same as "id not recoverable".
        if run_idx == 1:
            existing_cidfile = _cidfile_flag_value(command, run_idx)
            if existing_cidfile is not None:
                container_cidfile = Path(existing_cidfile)
            elif _command_has_cidfile_flag(command, run_idx):
                # An operator --cidfile is present but sits past an untabled
                # value-flag, so its value isn't reliably recoverable. Do NOT
                # inject a second one -- a duplicate --cidfile is malformed
                # (last-wins on docker, rejected on some podman versions). No
                # best-effort stop for this config, but detection already fired,
                # so the warn + round_container_orphan_risk event still holds the
                # no-silent-orphan floor.
                pass
            else:
                container_cidfile = log_path.with_name(log_path.name + ".cid")
                container_cidfile.unlink(missing_ok=True)  # docker/podman refuse an existing one
                container_cidfile_is_ours = True
                spawn_command = _inject_cidfile(command, run_idx, container_cidfile)
    # Defense-in-depth: config validation already rejects {prompt} in the
    # template for stdin mode, but run() must be safe even if called
    # directly with a mismatched template. In stdin mode, never substitute
    # {prompt} into argv — build it verbatim so the prompt cannot reach argv.
    argv = (
        list(spawn_command) + list(prompt_arg_template)
        if stdin_mode
        else _build_argv(spawn_command, prompt_arg_template, prompt)
    )
    # PWD pinned last — it mirrors cwd= (a correctness pin, not a knob), so
    # an [agent.env] PWD cannot silently diverge from where the child runs.
    env = {**os.environ, **env_extra, "PWD": str(work_dir)}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc: subprocess.Popen | None = None
    log_file = None
    try:
        log_file = log_path.open("w", encoding="utf-8")
        start = clock.monotonic()  # all round-duration/deadline math is monotonic (NTP-safe)
        last_progress_at = start
        proc = subprocess.Popen(
            argv,
            cwd=work_dir,
            env=env,
            stdin=subprocess.PIPE if stdin_mode else subprocess.DEVNULL,
            stdout=log_file,
            # Merged on purpose: oauth_fail / network_fail / network-blip detection
            # regex-scan stderr text out of this log (see hooks.agent_log_path).
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        if stdin_mode and proc.stdin is not None:
            # Write+close on a daemon thread so the wall-clock timeout loop below
            # is never blocked by a write >64KB (OS pipe buffer) to an agent that
            # doesn't drain stdin (R1128: no unbounded-hang path).
            stdin_pipe = proc.stdin
            stdin_data = prompt.encode("utf-8")

            def _write_stdin():
                try:
                    stdin_pipe.write(stdin_data)
                    stdin_pipe.close()
                except (BrokenPipeError, OSError):
                    pass  # agent exited before reading stdin; the poll loop handles exit

            threading.Thread(target=_write_stdin, daemon=True).start()
        result_seen_at: float | None = None
        grace_extended_emitted = False
        result_scan_offset = 0
        result_scan_carry = b""
        last_result_scan = start
        while True:
            ret = proc.poll()
            now = clock.monotonic()  # duration / R1128 hard-wall / grace / interval — all monotonic
            if ret is not None:
                duration = now - start
                return RunResult(exit_code=ret, duration_s=duration, timed_out=False, pid=proc.pid)
            if now - start > timeout_s:
                _terminate_agent(
                    proc,
                    clock,
                    container_runtime=container_runtime,
                    container_cidfile=container_cidfile,
                    on_container_orphan_risk=on_container_orphan_risk,
                )
                duration = clock.monotonic() - start
                exit_code = proc.returncode if proc.returncode is not None else -1
                return RunResult(
                    exit_code=exit_code, duration_s=duration, timed_out=True, pid=proc.pid
                )
            # Grace kill: result emitted but subprocess still running.
            if max_grace_after_result_s > 0:
                if result_seen_at is None and now - last_result_scan >= _RESULT_SCAN_INTERVAL_S:
                    last_result_scan = now
                    try:
                        with log_path.open("rb") as f:
                            f.seek(result_scan_offset)
                            chunk = f.read()
                        result_scan_offset += len(chunk)
                        haystack = result_scan_carry + chunk
                        if _RESULT_MARKER in haystack:
                            result_seen_at = now
                        else:
                            # Keep the tail so a marker split across two read
                            # chunks (this scan vs. the next) still re-forms.
                            result_scan_carry = haystack[-(len(_RESULT_MARKER) - 1) :]
                    except OSError:
                        pass  # log not flushed yet; retry next interval
                if result_seen_at is not None and now - result_seen_at > max_grace_after_result_s:
                    live, ignored = _live_children(proc, ignore_patterns=grace_kill_ignore_patterns)
                    if live:
                        # Busy: a backgrounded worker is still running. Don't
                        # reap — defer to the wall-clock ceiling. Signal once.
                        if not grace_extended_emitted:
                            if on_grace_extended is not None:
                                on_grace_extended(live, ignored)
                            grace_extended_emitted = True
                    else:
                        _terminate_agent(
                            proc,
                            clock,
                            container_runtime=container_runtime,
                            container_cidfile=container_cidfile,
                            on_container_orphan_risk=on_container_orphan_risk,
                        )
                        duration = clock.monotonic() - start
                        exit_code = proc.returncode if proc.returncode is not None else -1
                        return RunResult(
                            exit_code=exit_code,
                            duration_s=duration,
                            timed_out=True,
                            pid=proc.pid,
                            killed_for_grace=True,
                        )
            # Progress heartbeat: call back if interval elapsed
            if progress_callback is not None and progress_interval_s > 0:
                if now - last_progress_at >= progress_interval_s:
                    try:
                        st = log_path.stat()
                        log_size_kb = st.st_size // 1024
                        last_write_age_s = max(0, int(clock.epoch() - st.st_mtime))
                    except OSError:
                        log_size_kb = 0
                        last_write_age_s = 0
                    progress_callback(
                        {
                            "log_size_kb": log_size_kb,
                            "last_write_age_s": last_write_age_s,
                            "wall_age_s": int(now - start),
                        }
                    )
                    last_progress_at = now
            clock.sleep(0.2)
    except BaseException:
        # Supervisor death (a heartbeat callback raising, or a signal injected into
        # the round CLI) must not leave the agent pgroup orphaned. Reap, then re-raise
        # fail-loud — we never swallow the cause.
        if proc is not None:
            _terminate_agent(
                proc,
                clock,
                container_runtime=container_runtime,
                container_cidfile=container_cidfile,
                on_container_orphan_risk=on_container_orphan_risk,
            )
        raise
    finally:
        if log_file is not None:
            log_file.close()
        # Cleanup, not correctness: our own injected cidfile is a tiny,
        # round-scoped tmp file -- delete it once the round is fully done
        # (read-back for the best-effort stop, if any, already happened
        # above). Only ours: an operator-supplied --cidfile is never ours to
        # delete.
        if container_cidfile_is_ours and container_cidfile is not None:
            container_cidfile.unlink(missing_ok=True)
