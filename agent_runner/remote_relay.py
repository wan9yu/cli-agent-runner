"""Managed ssh event relay — the engine behind ``monitor --host X --mode events``.

The relay runs on the CLIENT (a laptop, a CI box), not on the supervised host.
It spawns

    ssh <host> -- agent-runner events --tail --kind <kinds> [--since <ts>] [--config <path>]

and passes the remote's JSON Lines stdout through unmodified. It owns the three
things a hand-rolled ``while true; ssh …; sleep 30; done`` loop gets wrong:

1. **Gap-free resume.** Every relayed line's ``ts`` is remembered; each RE-connect
   passes ``--since <last ts>`` so the events written while the link was down are
   replayed (at-least-once — the boundary event may repeat, none is lost). The
   first connect passes no ``--since``: streaming starts from now, matching
   ``events --tail``.
2. **Reconnect with a deadline.** Any ssh exit emits ``monitor_remote_blip`` and
   is retried with escalating backoff; if the link stays down past
   ``[monitor] remote_failure_tolerance_s`` the relay emits
   ``monitor_remote_giveup`` and exits 1 (a service manager restarts it).
   The failure clock resets on the first successfully relayed LINE — a
   connection that comes up and immediately dies is not recovery.
3. **Process hygiene.** ssh is spawned in its own session, and the whole process
   GROUP is torn down (SIGTERM → grace → SIGKILL) on interrupt, give-up and each
   reconnect — so no orphaned ssh/sleep tree survives a dropped link.

Detection is NOT relayed: the detectors run on the supervised host by design
(``auto_stop_on`` acts there with zero client involvement). This module moves
events, nothing else. The ``monitor_remote_blip`` / ``monitor_remote_giveup``
events it emits are the CLIENT's telemetry about its own link, and land in the
client's ``log_dir``.
"""

from __future__ import annotations

import json
import subprocess  # noqa: TID251 — the relay is the ssh spawner
import sys
import threading
import time
from collections import deque
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

# The relay's teardown is the same problem agent_runtime solves for agent
# processes: kill the session leader's whole group, SIGTERM → grace → SIGKILL.
# Importing it keeps one implementation of "leave no orphan tree behind".
from agent_runner.agent_runtime import _kill_pgroup
from agent_runner.events import (
    KNOWN_EVENT_KINDS,
    MONITOR_REMOTE_BLIP,
    MONITOR_REMOTE_GIVEUP,
    emit,
    parse_iso_ms,
)

_SSH = "ssh"
"""The ssh binary. Module-level so tests can point the relay at a fake-ssh
stub — the suite never spawns a real ssh at a real host."""

_RECONNECT_BACKOFF_S: tuple[float, ...] = (1, 2, 4, 8, 16, 30)
"""Sleep before each reconnect attempt, by attempt index (last value repeats).
Same escalation the polling monitor used, capped so a long outage retries once
every 30s instead of hammering a down host."""

_STDERR_TAIL_LINES = 20
"""ssh diagnostics kept for the blip/give-up event payload. Bounded because a
chatty remote must not grow the relay's memory."""


def default_kinds() -> list[str]:
    """Every event kind this client knows: built-ins plus locally-installed
    plugin kinds.

    ``events --tail`` requires an explicit ``--kind``, so the relay needs a
    default. This one is the closest honest equivalent of local ``--mode
    events`` (which streams everything): a kind that exists ONLY on the remote
    — a plugin installed there but not here — is not in this list and must be
    named with ``--kind``.
    """
    return sorted(KNOWN_EVENT_KINDS)


def _remote_argv(
    host: str,
    kinds: Sequence[str],
    remote_config: str | None,
    *,
    since: str | None,
) -> list[str]:
    """Full local argv: ssh, then the remote ``agent-runner events`` command."""
    remote = ["agent-runner", "events", "--tail", "--kind", ",".join(kinds)]
    if since is not None:
        remote += ["--since", since]
    if remote_config is not None:
        remote += ["--config", remote_config]
    # `--` ends ssh's own option parsing; everything after it is the remote command.
    return [_SSH, host, "--", *remote]


def _spawn(argv: list[str]) -> subprocess.Popen[str]:
    """Start ssh in its own session with SEPARATE stdout/stderr pipes.

    Separate, never merged: stdout is a JSONL contract the caller may pipe
    straight into ``jq``, and an ssh banner or "Connection closed" diagnostic
    on that stream would corrupt it.
    """
    return subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered: relay each event as it arrives, not per 8KB
        start_new_session=True,  # own process group — see _kill_pgroup teardown
    )


def _drain_stderr(proc: subprocess.Popen[str]) -> tuple[deque[str], threading.Thread]:
    """Pump ssh's stderr into a bounded tail on a background thread.

    A thread, not a post-mortem ``read()``: a full stderr pipe would block ssh
    forever while we sit blocked on stdout.
    """
    tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

    def _pump() -> None:
        if proc.stderr is None:  # pragma: no cover — PIPE is always requested
            return
        for line in proc.stderr:
            tail.append(line)

    thread = threading.Thread(target=_pump, daemon=True)
    thread.start()
    return tail, thread


def _line_ts(line: str) -> tuple[str, datetime] | None:
    """The event's ``ts`` as (raw, parsed), or None if the line is unusable.

    Unusable = not JSON, no ``ts``, or a ``ts`` that does not parse. Such a line
    is still relayed (the remote's stdout is the contract, not ours) but never
    becomes the resume point: a garbage timestamp would silently rewind or
    fast-forward every subsequent reconnect.
    """
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(evt, dict):
        return None
    ts = evt.get("ts")
    if not isinstance(ts, str):
        return None
    try:
        return ts, parse_iso_ms(ts)
    except ValueError:
        return None


def relay_remote_events(
    host: str,
    *,
    log_dir: Path,
    kinds: Sequence[str] | None = None,
    remote_config: str | None = None,
    failure_tolerance_s: float = 90.0,
    out: TextIO | None = None,
) -> int:
    """Relay a remote event stream to ``out``; returns a CLI exit code.

    Returns 0 on interrupt (SIGINT) and 1 when the link stays down longer than
    ``failure_tolerance_s``, printing why to stderr. ``failure_tolerance_s = 0``
    disables reconnection entirely: the first ssh exit gives up immediately
    (no blip).

    ``log_dir`` is the CLIENT's log dir — blip/give-up events describe this
    machine's link to ``host``, not the supervised project's health.
    """
    # ssh reads a leading '-' as an option (-oProxyCommand=… runs a local
    # command), so an attacker-supplied "host" must never reach the argv.
    if host.startswith("-"):
        raise ValueError(f"invalid ssh host (starts with '-'): {host!r}")
    resolved_kinds = list(kinds) if kinds else default_kinds()
    stream = out if out is not None else sys.stdout
    log_dir.mkdir(parents=True, exist_ok=True)

    last_ts: str | None = None
    last_dt: datetime | None = None
    blip_start: float | None = None
    attempt = 0
    proc: subprocess.Popen[str] | None = None

    try:
        while True:
            proc = _spawn(_remote_argv(host, resolved_kinds, remote_config, since=last_ts))
            stderr_tail, pump = _drain_stderr(proc)

            if proc.stdout is not None:  # pragma: no branch — PIPE is requested
                for line in proc.stdout:
                    stream.write(line if line.endswith("\n") else line + "\n")
                    stream.flush()
                    parsed = _line_ts(line)
                    if parsed is not None and (last_dt is None or parsed[1] > last_dt):
                        last_ts, last_dt = parsed
                    # A relayed line proves the link works end to end. Reset here,
                    # not on a successful spawn: an ssh that connects and dies
                    # before yielding anything is still an outage.
                    blip_start = None
                    attempt = 0

            # stdout EOF — ssh is finishing. Tear the whole group down before
            # reaping: killing after wait() could hit a recycled pgid, and a
            # ProxyCommand-style helper can outlive its ssh.
            _kill_pgroup(proc)
            returncode = proc.returncode
            proc = None
            pump.join(timeout=1.0)
            error = "".join(stderr_tail).strip()

            now = time.monotonic()
            if blip_start is None:
                blip_start = now
            attempt += 1
            elapsed = now - blip_start

            if failure_tolerance_s <= 0 or elapsed >= failure_tolerance_s:
                emit(
                    log_dir,
                    MONITOR_REMOTE_GIVEUP,
                    host=host,
                    returncode=returncode,
                    total_attempts=attempt,
                    total_elapsed_s=elapsed,
                    cap_s=failure_tolerance_s,
                    final_error=error,
                    resume_since=last_ts,
                )
                # The give-up is terminal, so it must not be silent on a
                # terminal — stdout stays pure JSONL, so this goes to stderr.
                # Blips do not: they are recoverable and recorded as events.
                print(
                    f"agent-runner: relay to {host!r} gave up after "
                    f"{elapsed:.0f}s / {attempt} attempts (ssh rc={returncode}): {error}",
                    file=sys.stderr,
                )
                return 1

            sleep_s = min(
                _RECONNECT_BACKOFF_S[min(attempt - 1, len(_RECONNECT_BACKOFF_S) - 1)],
                failure_tolerance_s - elapsed,
            )
            emit(
                log_dir,
                MONITOR_REMOTE_BLIP,
                host=host,
                returncode=returncode,
                error=error,
                attempt=attempt,
                elapsed_s=elapsed,
                cap_s=failure_tolerance_s,
                resume_since=last_ts,
                next_sleep_s=sleep_s,
            )
            time.sleep(sleep_s)
    except KeyboardInterrupt:
        return 0
    finally:
        if proc is not None:
            _kill_pgroup(proc)
