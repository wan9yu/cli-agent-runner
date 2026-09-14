"""Observation: peek / monitor_loop / narrate / stream / relay.

Imports ``_project_name`` and ``status`` from ``_lifecycle.py`` (the shared
one-directional dependency every slice of the old api.py takes on it).

Re-exported from ``agent_runner.api`` (the thin facade) so external callers
and existing internal callers keep working unchanged.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, TextIO

from agent_runner import defenses, events, monitor
from agent_runner._lifecycle import _project_name, status
from agent_runner.api_types import ProjectState, RateLimitState, select_path
from agent_runner.clock import SYSTEM_CLOCK
from agent_runner.config import load_config
from agent_runner.events import (
    AGENT_NETWORK_BLIP,
    CGROUP_GROWTH_RATE_WARNING,
    HOOK_FAILED,
    MONITOR_STARTED,
)

_RECENT_HOOK_FAILURES_LIMIT = 10
_RECENT_BLIPS_LIMIT = 5
_RECENT_GROWTH_LIMIT = 5
_MONITOR_SEEN_CAP = 512
"""Bound on `_monitor_loop_iter`'s dedup set — an unbounded set of alert-identity
keys would grow forever across a long-lived monitor process; oldest-episode
eviction (OrderedDict.popitem(last=False)) keeps memory flat."""


def _recent_events_of_kind(
    parsed_events: list[dict[str, Any]], kind: str, limit: int
) -> list[dict[str, Any]]:
    """Return the last ``limit`` events matching ``kind``, in chronological order.

    Walks the event list in reverse so we stop as soon as the limit is filled —
    parsed_events grows unboundedly over a project's lifetime; a full-scan
    comprehension here would dominate watch-loop peek cost.
    """
    out: list[dict[str, Any]] = []
    for e in reversed(parsed_events):
        if e.get("event") == kind:
            out.append(e)
            if len(out) == limit:
                break
    out.reverse()
    return out


def peek(
    project: str | Path | None = None,
    *,
    round: int | str | None = None,
    log: bool = False,
    events: int | None = None,
    select: str | None = None,
    cfg: Any = None,
) -> ProjectState | Any:
    """Build a ProjectState snapshot. With select, return that subtree.

    ``cfg`` (an already-loaded ``Config``) lets a caller that has loaded config
    once — ``cli.peek_cmd.cmd_peek`` does, for the ``emit`` block — pass it in so
    ``peek`` does not re-``load_config`` (which re-runs plugin load/checksum/probe)
    a second time per invocation. ``None`` loads it here (the default, unchanged
    for every other caller)."""
    from agent_runner import round_view

    work_dir = project if isinstance(project, Path) else Path.cwd()
    if cfg is None:
        cfg = load_config(work_dir / "agent-runner.toml")
    log_dir = cfg.runtime.log_dir
    src = monitor.LocalSource(log_dir=log_dir)
    base_state = monitor.assemble_project_state(src, project=_project_name(work_dir))
    parsed_events = monitor.parse_events_from_jsonl_files(src.events_files())
    round_num = round_view.resolve_round_arg(round, log_dir)
    current: Any = base_state.current_round
    if round_num is not None:
        current = round_view.build_round_view(log_dir, round_num, parsed_events, want_log=log)
        if current is None:
            raise KeyError(f"round {round_num} not found under {log_dir}/rounds/")
    recent = parsed_events[-events:] if events else []
    recent_hook_failures = _recent_events_of_kind(
        parsed_events, HOOK_FAILED, _RECENT_HOOK_FAILURES_LIMIT
    )
    recent_blips = _recent_events_of_kind(parsed_events, AGENT_NETWORK_BLIP, _RECENT_BLIPS_LIMIT)
    recent_cgroup_growth_warnings = _recent_events_of_kind(
        parsed_events, CGROUP_GROWTH_RATE_WARNING, _RECENT_GROWTH_LIMIT
    )

    from agent_runner._throttle import effective_throttle_view

    throttle, active = effective_throttle_view(log_dir)
    rate_limit: RateLimitState | None = None
    if throttle is not None:
        rate_limit = RateLimitState(
            throttled_until_epoch=throttle.reset_at_epoch,
            limit_type=throttle.classification,
            agent=throttle.agent,
            since_round=throttle.since_round,
            phase=throttle.phase,
            throttled_agents=tuple(sorted(active)),
        )
    # Resolve service state from the SAME project the events came from: work_dir
    # is the Path peek loaded cfg/log_dir from (a bare name falls back to cwd),
    # so status() can't drift to a sibling project's serve.pid.
    raw_service = status(work_dir)
    svc = dataclasses.replace(raw_service, rate_limit=rate_limit)

    state = ProjectState(
        project=base_state.project,
        status=base_state.status,
        defenses=[
            {
                "name": d.name,
                "value": d.value,
                "codifies": d.codifies,
                "guarded_by": str(d.guarded_by) if d.guarded_by else None,
                "current_state": d.current_state,
            }
            for d in defenses.catalog(cfg)
        ],
        current_round=current,
        recent_rounds=base_state.recent_rounds,
        orphan=base_state.orphan,
        system=base_state.system,
        service=svc,
        recent_events=recent,
        recent_hook_failures=recent_hook_failures,
        recent_blips=recent_blips,
        recent_cgroup_growth_warnings=recent_cgroup_growth_warnings,
        schedule=monitor.latest_schedule_state(parsed_events),
    )
    return state if select is None else select_path(state, select)


def _poll_once(
    project: str | Path, *, event_tail: monitor._EventTail | None = None
) -> list[monitor.Alert]:
    work_dir = project if isinstance(project, Path) else Path.cwd()
    cfg = load_config(work_dir / "agent-runner.toml")
    # Always local: detection runs on the supervised host by design. Remote
    # observation is an event RELAY (``monitor --host X --mode events``), not a
    # remote poll — see agent_runner/remote_relay.py.
    src: monitor.LocalSource = monitor.LocalSource(log_dir=cfg.runtime.log_dir)
    if event_tail is not None:
        events = event_tail.read(src.events_files())
    else:
        events = monitor.parse_events_from_jsonl_files(src.events_files())
    metrics = monitor.parse_events_from_jsonl_files(src.metrics_files())
    log_tails = monitor.load_round_log_tails(src.rounds_dir())
    builtin = monitor.run_all_detectors(
        events=events,
        metrics=metrics,
        log_tails=log_tails,
        round_budget_s=cfg.runtime.round_budget_s,
        supervisor_stale_threshold_s=cfg.monitor.supervisor_stale_threshold_s,
        auth_fail_patterns=cfg.monitor.auth_fail_patterns,
        auth_fail_hint=cfg.monitor.auth_fail_hint,
        phases_overrides=cfg.phases.overrides if cfg.phases.overrides else None,
        host_health_cfg=cfg.monitor.host_health,
        log_dir=cfg.runtime.log_dir,
    )
    if not monitor._PLUGIN_DETECTORS:
        return builtin  # skip ProjectState assembly when no plugins to feed
    state = monitor.assemble_project_state(src, project=_project_name(work_dir))
    plugin = monitor.run_plugin_detectors(state)
    return builtin + plugin


def monitor_loop(
    project: str | Path | None = None, *, host: str | None = None, interval_s: int = 30
) -> Iterator[monitor.Alert]:
    """Yield alerts as they're detected. Caller decides what to do.

    The loop dedups alerts by ``monitor.alert_identity`` (a stable per-episode key,
    not the raw measurement) within a bounded, oldest-evicted window so a single
    long-running alert cannot spam and the dedup set cannot grow unbounded.
    Emits ``monitor_started`` once at entry — programmatic consumers can subscribe
    to that kind as the canonical "supervision is up" signal (monitor is otherwise
    silent during healthy operation by design).

    ``host`` raises ``MonitorRemoteUnsupportedError`` immediately: detection runs
    on the supervised host by design, so there is no remote polling mode. The
    check is eager — this wrapper validates before handing back the generator,
    so the failure lands at startup rather than at the first ``next()``. For a
    remote event stream, see ``relay_remote_events``.
    """
    if host is not None:
        raise monitor.MonitorRemoteUnsupportedError(host)
    return _monitor_loop_iter(project, host=host, interval_s=interval_s)


class _FailOpenGuard:
    """Context manager for one of ``_monitor_loop_iter``'s supervision ops.

    agent-runner is a systemd-level supervisor: an exception raised inside
    the ``with`` block is the supervisor's OWN failure domain and must never
    end the monitor loop that noticed the problem. Swallows any ``Exception``
    (NOT ``BaseException`` — a ``KeyboardInterrupt``/``SystemExit`` still
    propagates), warns with the same ``{what}: {type(e).__name__}: {e}``
    shape every fail-open site hand-copied before this was unified, and sets
    ``.failed`` so the caller applies its OWN fallback afterward — this guard
    deliberately does NOT choose or homogenize what happens next: the poll
    site still sleeps-and-retries, the on_alert site still yields
    ``verdict = "failed"``, and the startup emit site takes no fallback at all.
    """

    def __init__(self, what: str) -> None:
        self.what = what
        self.failed = False

    def __enter__(self) -> _FailOpenGuard:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> bool:
        if exc is None or not isinstance(exc, Exception):
            return False  # nothing raised, or a BaseException we don't fail open on
        import warnings  # module-private: kept out of api's pinned public surface

        self.failed = True
        # stacklevel=3: __exit__ is itself one frame more than the
        # pre-unification inline `except Exception as e: warnings.warn(...,
        # stacklevel=2)` had, so this attributes the warning to the SAME
        # caller frame as before unification.
        warnings.warn(f"{self.what}: {type(exc).__name__}: {exc}", stacklevel=3)
        return True  # suppress: fail open, never crash the loop that noticed this


def _monitor_loop_iter(
    project: str | Path | None = None, *, host: str | None = None, interval_s: int = 30
) -> Iterator[monitor.Alert]:
    """Polling generator behind ``monitor_loop``.

    ``host`` is None by construction (``monitor_loop`` rejects anything else);
    it is carried into the ``monitor_started`` payload as an explicit record
    that this monitor watches its own host.
    """
    from collections import OrderedDict

    seen: OrderedDict[str, None] = OrderedDict()
    work_dir = project if isinstance(project, Path) else Path.cwd()
    cfg = load_config(work_dir / "agent-runner.toml")
    cfg.runtime.log_dir.mkdir(parents=True, exist_ok=True)
    with _FailOpenGuard("monitor_started emit failed"):  # abort-proof startup breadcrumb
        events.emit(
            cfg.runtime.log_dir,
            MONITOR_STARTED,
            host=host,
            interval_s=interval_s,
            log_dir=str(cfg.runtime.log_dir),
            mode="anomaly-only",
        )

    event_tail = monitor._EventTail()
    while True:
        poll_guard = _FailOpenGuard("monitor poll failed")
        with poll_guard:
            alerts = _poll_once(work_dir, event_tail=event_tail)
        if poll_guard.failed:  # a poll crash must not kill supervision
            SYSTEM_CLOCK.sleep(interval_s)
            continue
        for alert in alerts:
            key = monitor.alert_identity(alert)
            if key in seen:
                seen.move_to_end(key)
                continue
            seen[key] = None
            if len(seen) > _MONITOR_SEEN_CAP:
                seen.popitem(last=False)  # bounded: evict oldest episode
            yield alert
            # Pass the work_dir Path (not the bare project name): api.stop resolves
            # a name's log_dir cwd-dependently, so a monitor launched from a cwd !=
            # work_dir with a non-preset log_dir would target the wrong dir, see no
            # pidfile, and no-op while serve keeps running. The Path resolves to the
            # real cfg.runtime.log_dir.
            # on_alert is the stop path; a raise here (a failure domain the
            # supervisor exists to handle) must not end the generator and
            # leave serve unsupervised.
            verdict: monitor.OnAlertVerdict = "failed"
            with _FailOpenGuard("on_alert failed"):
                verdict = monitor.on_alert(
                    alert,
                    project=work_dir,
                    log_dir=cfg.runtime.log_dir,
                    allowed_stop_names=cfg.monitor.auto_stop_on,
                )
            if verdict == "draining":
                # Nothing was recorded for this alert this poll (see on_alert's
                # docstring) — force-clear its `seen` entry so it is NOT treated
                # as "still firing, stay suppressed" below: the alert condition
                # persisting is exactly the case that must re-fire on_alert next
                # poll, unlike every other verdict (recorded outcome, or never
                # eligible), which keeps the normal dedup.
                #
                # Trade-off: `seen` also gates the `yield alert` above (the
                # dedup consumers of the monitor stream see), so re-arming it
                # to retry on_alert ALSO re-yields this same alert to the
                # stream consumer next poll. Accepted: a persisting alert
                # re-appearing each poll while its stop drains just reads as a
                # "still draining" signal to that consumer. A fuller design
                # would split consumer-yield dedup from on_alert-retry state,
                # but that split isn't warranted for this narrow case.
                del seen[key]
        # Re-arm: an episode absent from this poll has cleared, so forget it — a
        # later recurrence is a NEW episode and must fire again (not stay suppressed
        # until bounded eviction). Only keys still firing this poll survive.
        current = {monitor.alert_identity(a) for a in alerts}
        for key in list(seen):
            if key not in current:
                del seen[key]
        SYSTEM_CLOCK.sleep(interval_s)


def narrate_events(log_dir: Path, *, poll_interval_s: float = 0.5) -> Iterator[str]:
    """Tail events-*.jsonl files in log_dir, yielding one formatted line per event.

    Format: ``[HH:MM:SS.fff] {event:<20} key=value ...`` (excluding ts and event).

    Polling-based (no inotify/kqueue — cross-platform). Designed for human-readable
    live monitoring during debug / audit / short runs. Yields events from byte 0
    of all files present at iterator start, then follows new appends.
    """
    # narrate_events -> _format_narrate_line does evt.get(...) -- a non-dict
    # line must not reach it (or stream_events_jsonl's machine-consumption
    # callers); _tail_events_jsonl's read_new composition already guards that.
    for evt in monitor._tail_events_jsonl(
        log_dir, start_at_now=False, poll_interval_s=poll_interval_s
    ):
        yield _format_narrate_line(evt)


def stream_events_jsonl(log_dir: Path, *, poll_interval_s: float = 0.1) -> Iterator[dict[str, Any]]:
    """Tail events-*.jsonl files in log_dir, yielding one parsed event dict per line.

    Subscription begins at "now": events present in the file before the iterator
    starts are NOT yielded. Follows file rotation transparently (when a new
    events-YYYY-MM.jsonl appears, the iterator picks it up from byte 0).

    Default poll_interval_s of 0.1 reflects machine-consumption latency
    expectations (vs ``narrate_events`` which uses 0.5 for human pacing).

    Polling-based (no inotify/kqueue — cross-platform). Designed for machine
    consumption (vs ``narrate_events`` which formats for humans).
    """
    yield from monitor._tail_events_jsonl(
        log_dir, start_at_now=True, poll_interval_s=poll_interval_s
    )


def relay_remote_events(
    host: str,
    *,
    log_dir: Path,
    kinds: Sequence[str] | None = None,
    remote_config: str | None = None,
    failure_tolerance_s: float = 90.0,
    out: TextIO | None = None,
) -> int:
    """Remote sibling of ``stream_events_jsonl``: relay ``host``'s event stream.

    Spawns a managed ``ssh <host> -- agent-runner events --tail`` and passes its
    JSONL through, reconnecting with ``--since`` so a dropped link replays its
    gap. Returns a CLI exit code (0 on interrupt, 1 once the link stays down
    past ``failure_tolerance_s``). Blocks until then.

    ``log_dir`` is this CLIENT's log dir: the ``monitor_remote_blip`` /
    ``monitor_remote_giveup`` events it writes describe the local machine's link
    to ``host``, not the supervised project. Detection is deliberately NOT
    relayed — the detectors run on ``host`` (see ``monitor_loop``).
    """
    # Late import: pulls agent_runtime (psutil, threading) only for the callers
    # that actually stream from a remote host.
    from agent_runner.remote_relay import relay_remote_events as _relay

    return _relay(
        host,
        log_dir=log_dir,
        kinds=kinds,
        remote_config=remote_config,
        failure_tolerance_s=failure_tolerance_s,
        out=out,
    )


def _format_narrate_line(evt: dict[str, Any]) -> str:
    """Format an event dict as a one-line human-readable string.

    Format: ``[HH:MM:SS.fff] {event:<20} {key=value pairs}``. ``ts`` and ``event``
    are extracted into the prefix; remaining top-level keys become ``key=value``.
    """
    ts = evt.get("ts", "")
    time_part = ts[11:23] if len(ts) > 23 else ts
    event = evt.get("event", "?")
    fields = {k: v for k, v in evt.items() if k not in ("ts", "event")}
    kv_parts = []
    for k, v in fields.items():
        if k == "round_num":
            # Cosmetic shorten: `round=N` is more scannable than `round_num=N` in
            # one-line narrate output. The wire field stays `round_num` in events.jsonl.
            kv_parts.append(f"round={v}")
        else:
            kv_parts.append(f"{k}={v}")
    kv = " ".join(kv_parts)
    return f"[{time_part}] {event:<20} {kv}"
