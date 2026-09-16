# Backlog

Known follow-ups that are real design or behavior decisions, not defects. Each
entry carries enough context to act on without re-deriving it.

Items marked **NEEDS_DESIGN** change user-visible behavior and are held for an
explicit decision — they are not shipped silently in a maintenance release.

**Ordered by priority** (highest first): live behavior / published-contract
decisions before structural cleanup, and a documented-unreachable known gap
last. The number is a rank, not a stable id.

## 1. `try_auto_commit` sweeps plugin-owned paths

`vcs_state.try_auto_commit` runs `git add -A` with an exclude pathspec built only
from the log directory (`_log_dir_exclude_pathspec`), so under
`vcs.dirty_action = "auto_commit"` paths registered via
`register_plugin_owned_paths` are committed along with the agent's work.

0.2.2 made the registry authoritative at the git boundary for `stash_orphan`
only (it folds `_owned_exclude_specs(repo)` into its pathspec); `try_auto_commit`
still does not. That fix discharged the specific promise in `docs/plugins.md`
that owned paths are not silently swept into an orphan stash. `auto_commit` is a
different promise with a different harm (a commit, not a hidden stash) and no
equivalent published guarantee.

This is plausibly an intentional choice — `auto_commit` means "commit
everything" — so track it as a **decision to confirm**, not a latent bug: should
`auto_commit` thread `_owned_exclude_specs` in to match `stash_orphan`, or is
committing a plugin deliverable alongside agent work the intended behavior?

## 2. `oom_kill_delta` is named differently on its two events — NEEDS_DESIGN

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
swap. The 0.2.19-era plan was to fold this into 0.3's anticipated event-model
canonicalization; 0.3 shipped as a config/plugin-ABI pivot (through 0.3.7,
memory.high) without that wholesale field rename, so this stays open and
unreconciled. No field name changes in code until that decision is made.

## 3. cgroup growth-rate detector has no action path — NEEDS_DESIGN

`host_health.cgroup_growth_rate_pressure` (the mid-round memory-growth-rate
floor, `[monitor.host_health.pressure] cgroup_growth_rate_warning_mb_per_min`,
default 512) is observability-only: it emits `cgroup_growth_rate_warning` but
never returns `critical`, never drives the `memory.high` soft-brake, and never
terminates a round. This is deliberate on two counts, both documented in-code
(`host_health.py` + `config/models.py`): it is intentionally NOT a fifth
fall-through tier of the `memory_pressure` ladder (PSI's early return there
would mask an otherwise-live growth-rate signal), and a growth-driven
termination path "needs calibration on a constrained host before it can safely
exist."

The open question is whether the growth-rate signal should ever *act* — brake
or terminate on a runaway allocation the PSI ladder hasn't caught yet — and at
what threshold, or whether it stays a pure warning for an operator/plugin to
act on. It bears directly on the memory-safety north star (prevent
unresponsiveness, not swapping), and it is gated on a constrained-host
calibration run of the kind the 0.3.7 soft-brake work established. Held until
that calibration answers it.

## 4. `anomaly_repetitive_*` default — NEEDS_DESIGN

`monitor.anomaly_repetitive_window` and `anomaly_repetitive_threshold` both
default to `0`, which disables the detector. This is a deliberate opt-in kill
switch, documented since the field's first commit, and the detector fires
correctly the moment an operator opts in (a `threshold <= window` validation
guard added since only reinforces the opt-in framing).

The open question is whether opt-in is the right default, or whether this belongs
in a preset. Flipping the default would arm a new alert for every existing
deployment. No preset has adopted the detector through 0.3.7, so the question is
genuinely still open.

## 5. `_tail_events` / `_tail_events_jsonl` duplication

Three event-stream tailers now exist at deliberately different scopes, and they
are not accidental copies:

- `cli/events_cmd.py:_tail_events` — current-month-scoped
  (`_current_month_events_file`), resets its offset to `0` on truncation.
- `_monitor_state.py:_tail_events_jsonl` — globs every `events-*.jsonl`;
  re-exported through the `monitor.py` facade. It moved out of `api.py` in the
  0.3.0 api split (`_install`/`_lifecycle`/`_observe`).
- `_throttle.py:_tail_events` — the newest two monthly files
  (`glob("events-*.jsonl")[-2:]`).

Collapsing them means first deciding which scope is correct for each caller — a
structural decision, not a literal swap. (The older "character-identical
kind-read loop in `_throttle.py` and `monitor.py`" sub-concern has evaporated:
`monitor.py` no longer carries that loop — it moved to `_monitor_detectors.py`
and grew its own reset-ladder logic — and `_throttle.py` iterates forward over
the tailer generator, so the only shared text left is the single line
`kind = ev.get("event")`.)

## 6. `stash_orphan` loses the ref when the post-push listing fails

`vcs_state.stash_orphan` returns `None` in three cases; two are true no-ops. The
third — `git stash push` succeeded but the follow-up `git stash list` failed —
means the WIP *is* stashed yet callers read "nothing stashed" and report the
tree as ignored. It is already documented in-code as a KNOWN GAP and judged
effectively unreachable (a listing that fails microseconds after a push that
just succeeded in the same repo).

Deferred because closing it is a naming decision, not cleanup: no event kind
carries the meaning "stashed but ref lost" (`orphan_stash_failed` would be wrong
— the stash exists). Left as-is until that kind is designed.
