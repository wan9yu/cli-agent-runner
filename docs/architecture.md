# Architecture

## Three-layer model

```
┌──────────────────────────────────────────┐
│ Layer 3: The Witness                     │  agent-runner monitor
├──────────────────────────────────────────┤
│ Layer 2: The Loop                        │  agent-runner serve (thin dispatcher)
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
| `startup_smoke_check` | R721 + #446 — _common.md frontmatter caused 4h/123-round silent burn; now halts serve (config_broken) instead of respawning a broken config | `tests/unit/test_serve_config_broken.py` |
| `flock_concurrency` | Architectural — prevent concurrent supervisors corrupting state | `—` |
| `atomic_state_writes` | Data integrity — crashes never leave half-written state files | `tests/invariants/test_atomic_write_enforced.py` |
| `event_kind_registry` | Prevent events.emit() typos / unregistered kinds slipping past CI | `tests/invariants/test_event_kind_registry.py` |
<!-- /gen:defenses-table -->

## Monitor: 11 detectors

Three categories by `auto_action`:

**Notify only** (severity `warning`):
`timeout_rate`, `hung`, `orphan_chain`, `disk_warning`, `mem_pressure`,
`smoke_fail_rate`, `network_fail`, `rate_limit_active`,
`anomaly_repetitive_active`, `supervisor_stale`.

**Auto-stop service** (severity `critical`, `auto_action="stop_service"`):
`oauth_fail`, `disk_critical`. Continuing in either state is harmful (burning
API quota / writing to a near-full disk).

<!-- gen:detector-list -->
- `anomaly_repetitive_active`
- `disk_critical` — **auto-stop**
- `disk_warning`
- `hung`
- `mem_pressure`
- `network_fail`
- `oauth_fail` — **auto-stop**
- `orphan_chain`
- `rate_limit_active`
- `supervisor_stale`
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

## Plugin injection: two paths

agent-runner has TWO independent mechanisms for plugins to influence the agent's prompt.
Operators sometimes conflate them. The flags are independent.

### Path 1: round-context.json prepend (controlled by `[prompt] inject_context`)

Before each round, the supervisor writes `round-context.json` to `{log_dir}/round-context.json`
with phase, round_num, plugin-provided context fields (from ContextEnricher), and
recent_events tail. If `[prompt] inject_context = true` (default), this JSON is prepended
to the agent's prompt file.

To disable this path: `[prompt] inject_context = false`.

### Path 2: PreRoundHook mutation (controlled by `[runtime] disable_pre_round_hooks`)

Before each round, the supervisor invokes every registered PreRoundHook (from plugin
entry_points in `agent_runner.pre_round_hooks` group). These hooks receive a HookContext
and can read OR mutate `cfg.prompt.file` (or its contents directly).

To disable this path: `[runtime] disable_pre_round_hooks = true`.

When a PreRoundHook mutates the prompt content (sha256 changes), a `prompt_overwritten`
event is emitted with `hook=<name>`, `old_hash`, `new_hash` — operator can grep this to
audit plugin behavior.

### The two flags are independent

Setting `inject_context = false` does NOT disable PreRoundHooks. Setting
`disable_pre_round_hooks = true` does NOT disable the round-context.json prepend.

If you want neither injection: set both. If you want to disable a specific plugin
hook (vs ALL pre-round hooks), use `[plugins] disable = ["that_entry_point_name"]`.

## Known event kinds

<!-- gen:event-kinds -->
- `agent_exit`
- `agent_network_blip`
- `agent_self_terminated`
- `agent_spawn`
- `agent_usage_recorded`
- `anomaly_repetitive_tool`
- `config_broken`
- `dirty_commit_failed`
- `dirty_detected`
- `fresh_eyes_round_triggered`
- `hook_failed`
- `max_rounds_reached`
- `monitor_alert_emitted`
- `monitor_auto_stop_failed`
- `monitor_auto_stop_triggered`
- `monitor_remote_blip`
- `monitor_remote_giveup`
- `monitor_started`
- `orphan_idempotent_skip`
- `orphan_stash_failed`
- `orphan_stashed`
- `package_upgraded`
- `prompt_overwritten`
- `round_end`
- `round_grace_extended`
- `round_grace_kill`
- `round_progress`
- `round_start`
- `round_substrate_after`
- `round_substrate_before`
- `round_timeout_kill`
- `serve_startup_hook_failed`
- `service_upgrade_rollback_failed`
- `service_upgrade_rolled_back`
- `service_upgraded`
- `sigterm_received`
- `smoke_check_failed`
- `status_recovered`
- `stop_file_detected`
- `transient_error_backoff_capped`
- `transient_error_detected`
- `transient_error_recovered`
<!-- /gen:event-kinds -->

## 中文摘要

三层架构：Round（一轮 agent）/ Loop（serve 薄壳）/ Witness（monitor）。
三视角对称：peek（快照）/ watch（快照循环）/ monitor（异常检测），共用下钻参数。
防御以结构化目录形式存在（11 条），每条防御自描述「防的是哪条历史教训、被哪个 invariant test 守、当前状态」。
