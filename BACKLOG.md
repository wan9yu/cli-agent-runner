# Backlog

Known follow-ups and candidate work — real design or behavior decisions, not
defects. Each entry carries enough context to act on without re-deriving it.

Items marked **NEEDS_DESIGN** change user-visible behavior and are held for an
explicit decision — they are not shipped silently in a maintenance release.

**Ordered by priority** (highest first). The number is a rank, not a stable id.
Priority follows the sharpest differentiator: prove and deepen host-health /
cooperative pre-OOM supervision on constrained hosts first (items 1–2); the
cognitive / goal-adherence layer (item 3) builds on that same control loop;
cross-CLI resume breadth and reach are lower.

## 1. Prove cooperative pre-OOM drain end-to-end on a constrained cgroup — NEEDS_DESIGN

The host-health mechanisms built across the 0.3.x line — the cgroup
memory-growth-rate detector (0.3.1), the `memory.high` soft-brake (0.3.7), and
the typed `cooperative_stop` signal (0.3.9) — have never been demonstrated
*together*, end-to-end, under real memory pressure on a small (~462 MB) cgroup.
The load-bearing property is: the agent is cooperatively drained **before** the
kernel OOM killer fires. It is provable with a synthetic memory-growing child
under a constrained cgroup — no live model needed. Build that harness, verify
the full detect → brake → drain loop, and pin the small-host calibration
(swap-floor + PSI thresholds) and reap-safety (PID-reuse, orphan reap) as a
durable, tested execution guarantee. Making this claim demonstrable is the
highest-value work on the list; everything below is secondary to it.

## 2. Runaway session-footprint host-health signal — NEEDS_DESIGN

A resumed CLI session file (e.g. pi's session `.jsonl`) grows unbounded across
rounds — a disk / inode pressure surface only the supervisor can see, and one
that cross-round resume (0.3.10) amplifies. Add a host disk/inode pressure
detector as a leading indicator so the pre-OOM brake can fire earlier and more
specifically. Frame it as a *host* signal (any growing per-agent footprint),
not a single-CLI feature.

## 3. Cognitive / goal-adherence supervision loop (observe → assess → steer → persist) — NEEDS_DESIGN

Today the supervisor watches *resource* health (memory / host) and *mechanical*
progress (no-usage, substrate fingerprint, repetitive-tool). It cannot tell when
an agent is "on a treadmill" — busy (produces output, edits files, exits 0) but
not actually advancing toward its goal. Add a second, **advisory** layer to the
same round-loop: a plugin observes each round, assesses progress toward a
declared goal, and — only when warranted — injects a short, grounded advisory
into the *next* round's context (through the existing `inject_context` path) that
the agent may heed or dismiss.

Design spine (composes as a fold over the durable event bus + `inject_context`,
**not** a new live callback — the round is a fresh subprocess, so cross-round
state is file/event-based; this re-adds a feedback seam an earlier release
removed for lack of a producer, but strictly narrower: a data-return / fold, not
arbitrary context mutation):

- A first-class `[goal]` config (the "goal command"). PRIMARY verification is
  **objective**: operator-declared `goal.checks` (a command the supervisor runs
  each round — exit 0 = satisfied, an optional numeric target) and milestones,
  run through the reap-safe / timeout / killpg machinery so a check can never
  orphan or blow the cgroup. An optional, objective-gated LLM assessor is a last
  resort, never in the base install.
- A zero-cost default assessor: an objective **"treadmill signature"**
  (activity-high + net-convergence-low, from signals already emitted; requires
  persistence over K rounds + affirmative evidence, so false positives stay near
  zero).
- **Hard invariant — the advisory / kill firewall:** the advisory path is
  strictly separate from the give-up / kill decision. A possibly-wrong assessor
  can steer but can NEVER influence a kill (a distinct event kind the kill path
  never reads, plus an import firewall; the assessment type structurally has no
  kill field). Killing stays on the conservative mechanical path.
- Steering is advisory only: a fixed schema that cannot express a command
  (grounded observation + a question + confidence + a respected one-line
  dismissal), edge-triggered, one concern at a time, dismiss-and-respect so it
  never nags.
- CLI-agnostic: reads the agent's merged log + the goal; works around any CLI.
- Persistence rides a supervisor-owned, bounded, append-only lessons ledger
  re-injected each round (subordinate to the disk host-health signal in item 2).
  The supervisor never edits the agent's own memory files — it may *suggest* the
  agent record a lesson.

Sequence this after items 1–2: it is a second consumer of the same
observe → assess → act control loop (mechanical actuator kills, advisory
actuator steers), and it earns its slot only once the resource edge is proven.

## 4. SECURITY containment guidance

agent-runner is lifecycle-safety + observability, not containment — a
deliberate boundary that is currently undocumented, so it reads as a gap. Add a
SECURITY section that states the division plainly and gives the operator
concrete containment guidance: a dedicated unprivileged user, a container/VM via
`exec_prefix`, egress limits, no passwordless sudo, read-only mounts. Docs-only.

## 5. claude cross-round resume — NEEDS_DESIGN

0.3.10 shipped resume for pi only (its `--session-id` is idempotent —
create-or-resume). claude's `--session-id` is create-only and `--resume` is a
separate flag, so it needs a create-vs-resume state machine gated on the prior
round's outcome (a crashed round re-creates rather than `--resume`-errors), plus
an empirical check that `claude -p --resume <id>` with the prompt on stdin
actually resumes **and** runs the new turn (rather than silently no-opping).
Keep it a thin per-CLI declaration; if it forces heavy session-state ownership,
that is the signal it belongs at the CLI layer, not here.

## 6. Surface resume in the preflight (doctor / peek) — NEEDS_DESIGN

resume is the one manifest-declared capability not shown by `doctor` / `peek`
(unlike `cooperative_stop` and the SIGTERM grace). An operator on a
resume-capable preset has no preflight way to confirm resume will engage for
their phase config — in particular the deliberate cold-start on an ambiguous
phase is silent until they notice no `session_resumed` events. Add a preflight
line (with a "resume will not engage — ambiguous phase" note). Requires a `peek`
schema bump.

## 7. Resume for gemini / codex / kimi — NEEDS_DESIGN

Each has a different resume shape (gemini resumes by index/latest, not a chosen
id; codex uses a subcommand plus a captured id). Extending resume means widening
the manifest `resume_flag` into a small sum type with a per-variant injection at
the argv-build site — never optional fields bolted onto one struct. Ship only
the variants whose resume is verified to work end-to-end.

## 8. Fleet fan-in view — NEEDS_DESIGN

Aggregate several supervised hosts into one place to watch, built on the
existing remote-relay primitives — the density story (many cheap / constrained
boxes under one supervisor). A larger effort; sequence it after the pre-OOM edge
(item 1) is proven, so it fans in verified hosts rather than claimed ones.

## 9. Runtime & distribution: single-binary edge install (language) — longer-horizon, gated

Python is the right foundation for the *plugin* surface — dynamic per-CLI
plugins and the declared-capability manifest are a Python strength, and "cover
every CLI via a plugin" is a core stance; a wholesale Rust/Go rewrite would
regress exactly that. The one place another language would genuinely help is
**distribution**: a static single binary ("copy one file, run it", no
interpreter or install step) suits constrained / edge hosts better than a Python
runtime plus a dependency. Footprint and speed are *not* reasons — the
supervisor's ~24 MB RSS is negligible against a GB-scale agent, and all internal
scheduling is already event-based (run time is model-latency-bound, not
CPU-bound in this codebase).

Held for an explicit decision, gated on two triggers: (a) the host-health /
pre-OOM edge (item 1) is proven and stable — you do not rewrite a moving,
unproven core; and (b) a distribution benchmark shows single-binary install is
the binding constraint. Even then, try Python-native single-binary packaging
first (a self-contained bundle) before any rewrite. If a rewrite is ever
warranted, the realistic shape is a small, stable core of the host-health / reap
logic in Rust or Go with the plugin layer staying Python or a declarative
format — a hybrid whose language boundary is the hard part, not a full port.

## 10. Per-CLI session-directory resolution

Resolve each CLI's session directory via its own environment precedence rather
than a hardcoded default (never assume `~/.pi` / `~/.claude` / etc.). Ties to
the existing work_dir-resolution work.

## 11. Test the resume boot-guard's per-phase branch

The boot guard that rejects a static resume flag already present in a command is
tested for the base agent but not the per-phase `[phases.<name>.agent]` override
branch. The override loop is correct by construction but untested — one small
test.

## 12. `_tail_events` / `_tail_events_jsonl` duplication

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

## 13. `stash_orphan` loses the ref when the post-push listing fails

`vcs_state.stash_orphan` returns `None` in three cases; two are true no-ops. The
third — `git stash push` succeeded but the follow-up `git stash list` failed —
means the WIP *is* stashed yet callers read "nothing stashed" and report the
tree as ignored. It is already documented in-code as a KNOWN GAP and judged
effectively unreachable (a listing that fails microseconds after a push that
just succeeded in the same repo).

Deferred because closing it is a naming decision, not cleanup: no event kind
carries the meaning "stashed but ref lost" (`orphan_stash_failed` would be wrong
— the stash exists). Left as-is until that kind is designed.
