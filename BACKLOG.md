# Backlog

Known follow-ups that are real design or behavior decisions, not defects. Each
entry carries enough context to act on without re-deriving it.

Items marked **NEEDS_DESIGN** change user-visible behavior and are held for an
explicit decision — they are not shipped silently in a maintenance release.

**Ordered by priority** (highest first). The number is a rank, not a stable id.

## 1. `_tail_events` / `_tail_events_jsonl` duplication

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

## 2. `stash_orphan` loses the ref when the post-push listing fails

`vcs_state.stash_orphan` returns `None` in three cases; two are true no-ops. The
third — `git stash push` succeeded but the follow-up `git stash list` failed —
means the WIP *is* stashed yet callers read "nothing stashed" and report the
tree as ignored. It is already documented in-code as a KNOWN GAP and judged
effectively unreachable (a listing that fails microseconds after a push that
just succeeded in the same repo).

Deferred because closing it is a naming decision, not cleanup: no event kind
carries the meaning "stashed but ref lost" (`orphan_stash_failed` would be wrong
— the stash exists). Left as-is until that kind is designed.
