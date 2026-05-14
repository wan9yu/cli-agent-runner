# Architecture

## Three-layer model

```
┌──────────────────────────────────────────┐
│ Layer 3: The Witness                     │  agent-runner monitor
├──────────────────────────────────────────┤
│ Layer 2: The Loop                        │  agent-runner serve (≤60 LOC)
├──────────────────────────────────────────┤
│ Layer 1: The Round                       │  agent-runner round
└──────────────────────────────────────────┘
```

Each layer can run without the layer above. The Witness can watch a remote
Loop.

**Provider-agnostic by design.** The reference agents are `claude` (default
preset) and `aider` (added in 0.1.7) because those are what we run in
production, but the supervisor's defenses, observability, and lifecycle make
no CLI-specific assumptions in core. Set `[agent].command` to any prompt-arg
CLI and the same Round / Loop / Witness layers apply.

## Three-view symmetry (operator surface)

| View | Mental model | Command |
|---|---|---|
| Snapshot | "facts about now" | `peek` |
| Snapshot × time | "facts about now (auto-refresh)" | `watch` |
| Anomalies × time | "what changed, what's not normal" | `monitor` |

All three accept the same drill-down flags: `--round N`, `--log`, `--events N`,
`--json`, `--select PATH`. Operator learns one mental model, three lenses.

## Defenses-as-data

`agent_runner.defenses.catalog(cfg)` returns 11 structured `Defense` entries.
Each entry carries:

- `name` — stable identifier
- `value` — current configured / runtime value
- `codifies` — which historical incident motivates this defense
- `guarded_by` — the invariant test that prevents regression
- `current_state` — `active` | `degraded` | `off`

The catalog is the **single source of truth**. `peek`, `status`, and the
start banner all read it. Adding a new defense = one entry here + automatic
surfacing everywhere.

## Defense roster

<!-- gen:defenses-table -->
| Defense | Codifies | Guarded by |
|---|---|---|
| `round_timeout_s` | R1128 — TaskOutput polling loop 60min, scheduler grace fails to trigger | `—` |
| `process_group_isolation` | #307 — process group reaping for descendant cleanup | `tests/unit/test_agent_runtime.py` |
| `sigterm_reaper` | R725 — SIGTERM-during-round dual-claude race | `—` |
| `orphan_stash_idempotency_s` | R820 — same-second 3 phantom stashes | `—` |
| `sha_locked_stash` | §9 IMMUTABLE — batch drop by index breaks under concurrent stash | `tests/invariants/test_stash_uses_sha_not_index.py` |
| `set_diff_classification` | R2110 — rotation-only diff via +-line scan misclassifies | `—` |
| `critical_envs_injection` | Env injection via [agent.env] block — preset-supplied per CLI (e.g. DISABLE_AUTOUPDATER for claude prevents mid-loop self-updates) | `—` |
| `startup_smoke_check` | R721 + #446 — _common.md frontmatter caused 4h/123-round silent burn | `—` |
| `flock_concurrency` | Architectural — prevent concurrent supervisors corrupting state | `—` |
| `atomic_state_writes` | Data integrity — crashes never leave half-written state files | `tests/invariants/test_atomic_write_enforced.py` |
| `event_kind_registry` | Prevent events.emit() typos / unregistered kinds slipping past CI | `tests/invariants/test_event_kind_registry.py` |
<!-- /gen:defenses-table -->

## Monitor: 9 detectors

Three categories by `auto_action`:

**Notify only** (severity `warning`):
`timeout_rate`, `hung`, `orphan_chain`, `disk_warning`, `mem_pressure`,
`smoke_fail_rate`, `network_fail`.

**Auto-stop service** (severity `critical`, `auto_action="stop_service"`):
`oauth_fail`, `disk_critical`. Continuing in either state is harmful (burning
API quota / writing to a near-full disk).

<!-- gen:detector-list -->
- `disk_critical` — **auto-stop**
- `disk_warning`
- `hung`
- `mem_pressure`
- `network_fail`
- `oauth_fail` — **auto-stop**
- `orphan_chain`
- `smoke_fail_rate`
- `timeout_rate`
<!-- /gen:detector-list -->

## Monitor: anomaly-only by design

The monitor emits no events during healthy operation — it surfaces alerts only when a detector fires. To verify the monitor process is running, look for the `monitor_started` event in `events-*.jsonl`. Programmatic consumers (e.g. supervisory layers like Argus Gateway) should subscribe to that event kind as the canonical "supervision is up" signal. The event carries `mode: "anomaly-only"` to document the intentional silence.

## Monitor: transient ssh tolerance

`monitor --host <alias>` tolerates short ssh-protocol failures (rc=255) during
steady-state polling. The tolerance window defaults to 90 seconds and is
configurable via `[monitor] remote_failure_tolerance_s` (set to 0 to disable).
Backoff is 1s → 2s → 4s → ... → 30s. During the window each failed poll emits a
`monitor_remote_blip` event; if the window expires without recovery a
`monitor_remote_giveup` event is emitted before the error propagates (CLI
exits 1; systemd restarts the process). The two-event scheme makes postmortem
grep cleaner than a single event with a `final=true` flag.

## Known event kinds

<!-- gen:event-kinds -->
- `agent_exit`
- `agent_network_blip`
- `agent_spawn`
- `dirty_detected`
- `hook_failed`
- `monitor_alert_emitted`
- `monitor_auto_stop_failed`
- `monitor_auto_stop_triggered`
- `monitor_remote_blip`
- `monitor_remote_giveup`
- `monitor_started`
- `orphan_idempotent_skip`
- `orphan_stash_failed`
- `orphan_stashed`
- `prompt_overwritten`
- `round_end`
- `round_start`
- `round_timeout_kill`
- `sigterm_received`
- `smoke_check_failed`
- `status_recovered`
<!-- /gen:event-kinds -->

## 中文摘要

三层架构：Round（一轮 agent）/ Loop（serve 薄壳）/ Witness（monitor）。
三视角对称：peek（快照）/ watch（快照循环）/ monitor（异常检测），共用下钻参数。
防御以结构化目录形式存在（11 条），每条防御自描述「防的是哪条历史教训、被哪个 invariant test 守、当前状态」。
