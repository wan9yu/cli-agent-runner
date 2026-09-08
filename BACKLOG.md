# Backlog

Known follow-ups that are real design or behavior decisions, not defects. Each
entry carries enough context to act on without re-deriving it.

Items marked **NEEDS_DESIGN** change user-visible behavior and are held for an
explicit decision — they are not shipped silently in a maintenance release.

## `try_auto_commit` sweeps plugin-owned paths

`vcs_state.try_auto_commit` runs `git add -A` with an exclude pathspec built only
from the log directory (`_log_dir_exclude_pathspec`), so under
`vcs.dirty_action = "auto_commit"` paths registered via
`register_plugin_owned_paths` are committed along with the agent's work.

0.2.2 made the registry authoritative at the git boundary for `stash_orphan`
only. That fix discharged the specific promise in `docs/plugins.md` that owned
paths are not silently swept into an orphan stash. `auto_commit` is a different
promise with a different harm (a commit, not a hidden stash) and no equivalent
published guarantee, so it needs its own call: should `auto_commit` honor the
registry, or is committing a plugin deliverable alongside agent work the
intended behavior?

## `_tail_events` / `_tail_events_jsonl` duplication

`cli/events_cmd.py:_tail_events` and `api.py:_tail_events_jsonl` both tail the
event stream, deliberately at different scopes: `api` globs every
`events-*.jsonl`; `events_cmd` is current-month-scoped and resets its offset to
0 on truncation. They are not accidental copies. Collapsing them means first
deciding which scope is correct for each caller.

Related, from the same area: the kind-read loops in `_throttle.py` and
`monitor.py` (`kind = ev.get("event")` over `reversed(events)`) are
character-identical after the 0.2.2 constant normalization. Extracting a shared
helper is a structural change, not a literal swap.

## Detector fixtures pin raw event-kind literals

Detector test fixtures construct events from hardcoded strings (`{"event":
"round_start"}`) rather than emitting through `events.emit()`. Fixture and
reader therefore agree with each other while both can diverge from what the
supervisor actually writes.

Proven by mutation: point the emitter at a different kind value and the 28
`test_claude_error_detector.py` tests stay green while every detector is blind.
The 0.2.2 constant normalization does not close this — it moves the pin from the
reader to the fixture. Closing it means fixtures emitting through
`events.emit()`, or an end-to-end test asserting a real round produces events
the detectors actually match.

Bounded by `docs/events.md`, which forbids renaming a kind value outright (the
kind name is the version discriminator; a breaking change ships as
`round_start_v2`). Reaching this failure requires a contract violation — but the
suite would not report it.

## `remote_failure_tolerance_s` does not cover `subprocess.TimeoutExpired` — NEEDS_DESIGN

The tolerance window covers ssh exit code 255 only. `monitor.run_remote_command`
raises `MonitorRemoteError` on rc=255, and `api.monitor_loop`'s retry loop
catches exactly that. A hung (not failed) ssh connection instead trips the
`subprocess.run(timeout=...)` guard and raises `subprocess.TimeoutExpired`,
which bypasses the window and kills the monitor.

The rc=255-only scope is intentional and pinned by the monitor-signals design,
and the documentation matches the code exactly — there is no drift here. Widening
it changes a documented recovery path, so it is a design decision, not debt.

## `anomaly_repetitive_*` default — NEEDS_DESIGN

`monitor.anomaly_repetitive_window` and `anomaly_repetitive_threshold` both
default to `0`, which disables the detector. This is a deliberate opt-in kill
switch, documented since the field's first commit, and the detector fires
correctly the moment an operator opts in.

The open question is whether opt-in is the right default, or whether this belongs
in a preset. Flipping the default would arm a new alert for every existing
deployment.

## `stash_orphan` loses the ref when the post-push listing fails

`vcs_state.stash_orphan` returns `None` in three cases; two are true no-ops. The
third — `git stash push` succeeded but the follow-up `git stash list` failed —
means the WIP *is* stashed yet callers read "nothing stashed" and report the
tree as ignored. It is already documented in-code as a KNOWN GAP and judged
effectively unreachable (a listing that fails microseconds after a push that
just succeeded in the same repo).

Deferred because closing it is a naming decision, not cleanup: no event kind
carries the meaning "stashed but ref lost" (`orphan_stash_failed` would be wrong
— the stash exists). Left as-is until that kind is designed.

## `_monitor_loop_iter`'s three fail-open guards are hand-duplicated

`api._monitor_loop_iter` wraps the startup `monitor_started` emit, the
per-tick poll, and `on_alert`'s per-alert dispatch each in its own
`try/except Exception: warnings.warn(...)` block so a crash in any one of the
three never ends monitor supervision. The three guards are structurally
identical (catch, format a `type(e).__name__: {e}` warning, keep the loop
alive) but hand-copied rather than sharing one implementation; `on_alert`'s
own `emit()` closure already shows the pattern for factoring a shared guard.

Collapsing them to one shared guard is behavior-preserving — it must not
change which failures warn vs which propagate, nor the specific fallback
each site takes on failure (sleep-and-retry after a poll failure,
`verdict = "failed"` after an `on_alert` failure). Slated for 0.2.19.

## `StateSource(Protocol)` has exactly one implementation

`_monitor_state.py` declares `StateSource` as a `Protocol` with one concrete
implementation, `LocalSource`, and one construction site; `remote_relay.py`
solved remote monitoring a different way and never implements the Protocol.
An unimplemented seam like this is a real question for 0.3 (does
per-agent/per-host monitoring in the plugin era need a second
implementation?) but as written today it buys nothing over the concrete
type.

Default is to collapse it to `LocalSource` directly — vulture's dead-symbol
gate requires the deletion and its last reference removed in the same
commit — unless a check against the 0.3 direction shows the seam is
genuinely load-bearing there, in which case keep it with a one-line
"declared 0.3 seam" note instead of a bare unused abstraction. Slated for
0.2.19.

## `_live_children` can leak a secret through `argv[0]` despite the basename-only intent

`agent_runtime._live_children` (agent_runtime.py:131) stores only
`Path(argv[0]).name` for each descendant process specifically because full
argv is where secrets leak (`PGPASSWORD=…`, `--api-key …`,
`redis://:pass@…`) into the persisted `events-*.jsonl` stream — the
function's own docstring states this intent. But `Path(...).name` only
strips path *components*; if a process rewrites its own `argv[0]` to
something that isn't a filesystem path at all (argv-rewriting can embed
arbitrary text, including connection strings or tokens, with no `/` in it),
`.name` returns the whole string unchanged and the secret still reaches
disk.

Closing the gap means minimizing/bounding what's stored regardless of
whether `argv[0]` looks like a path (e.g. falling back to the
kernel-reported process name for untrusted children, or capping length)
rather than trusting `Path(...).name` alone. Security-low; a fix and test
are slated for 0.2.19.

## relay CLI has no SIGTERM handler — NEEDS_DESIGN

The relay's process-group teardown convention (SIGTERM → grace → SIGKILL)
governs how the relay tears down *its own* child group on interrupt,
give-up, and each retry — but the relay CLI process itself installs no
SIGTERM handler, so sending it SIGTERM (the normal way a process manager or
`systemctl stop` asks a process to shut down) hits Python's default
disposition: immediate termination, skipping the same clean-teardown path
the relay already uses internally and that `serve`'s own SIGTERM handling
follows.

Making the relay catch SIGTERM and shut down through its normal drain path
is user-visible: it changes what happens to the relay's child group and
exit behavior when something external sends it SIGTERM, for anyone
currently relying on — knowingly or not — the immediate-kill default.
Tagged NEEDS_DESIGN because the target shutdown semantics (drain vs.
immediate, and how much grace) need an explicit decision, not a silent
behavior change. A fix matching the `serve` pattern is targeted for 0.2.19;
if the semantics aren't settled by ship, this stays open.

## `_plugin_scan` misses egg-info/zip-installed dists and mishandles extras-suffixed entry points

`_plugin_scan._parse_entry_points_files` only globs `*.dist-info`
directories on each `sys.path` entry; legacy `*.egg-info` layouts (older
setuptools installs, some system packages) and zip-safe eggs are never
globbed, so a plugin installed that way is invisible to the fast path —
silently, since an empty scan result isn't an exception and so never trips
the `AGENT_RUNNER_PLUGIN_DISCOVERY=metadata` fallback in
`scan_entry_points` on its own; an operator has to already know to set it.

Separately, an entry-point value can carry an extras marker suffix
(`module:attr [extra1,extra2]`, from a package's extras-gated entry point)
which `importlib.metadata.EntryPoint.value` strips automatically but the
raw `value.partition(":")` parsing in `__init__.py:_load_plugins_from_group`
does not — leaving the suffix text glued onto the attribute path and
breaking `getattr` resolution for exactly the plugins that declare
themselves this way.

Both are plugin-discovery hardening ahead of 0.3's plugin surface; each
needs its own parity test. Slated for 0.2.19.

## `oom_kill_delta` is named differently on its two events — NEEDS_DESIGN

The same cgroup `memory.events.oom_kill` delta is emitted under two
different field names depending on which event carries it: `round_cgroup_memory`
reports it as `events_oom_kill_delta` (matching its sibling counters
`events_high_delta`/`events_max_delta`/`events_oom_delta`), while
`round_oom_killed` — folded from the same scan, no second sysfs read —
reports the identical value as bare `oom_kill_delta`. A reader correlating
the two events for the same round has to know the same quantity carries two
names.

Renaming either side is a change to a published event field name — part of
the peek/event JSON contract — so which name becomes canonical (and whether
it lands as a rename or an additive alias) needs a decision, not a silent
swap. Tagged NEEDS_DESIGN. A reconciliation (updating the emit call, the
runbook, and any golden fixture) is targeted for 0.2.19; if the naming
decision isn't made by ship, this stays open.
