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
