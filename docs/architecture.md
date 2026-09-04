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

Each layer can run without the layer above. The Witness runs on the host it
watches; a client elsewhere relays its event stream (see "Remote observation"
below).

**Provider-agnostic by design.** 6 presets ship (`claude` — the default —
plus `aider`, `gemini`, `codewhale`, `kimi` and `pi`) because those are what we run in
production, but the supervisor's defenses, observability, and lifecycle make
no CLI-specific assumptions in core. Set `[agent].command` to any prompt-arg
CLI and the same Round / Loop / Witness layers apply.

## Three-view symmetry (operator surface)

| View | Mental model | Command |
|---|---|---|
| Snapshot | "facts about now" | `peek` |
| Snapshot × time | "facts about now (auto-refresh)" | `watch` |
| Anomalies × time | "what changed, what's not normal" | `monitor` |

`peek` and `watch` accept the same drill-down flags: `--round N`, `--log`,
`--events N`, `--select PATH`. `monitor` is the anomaly lens and takes its own
flags (`--mode`, `--host`, `--interval`, `--kind`, `--remote-config`,
`--port`); `--config` and `--json` are
common to all three. Operator learns one mental model, three lenses.

## Defenses-as-data

`agent_runner.defenses.catalog(cfg)` returns 14 structured `Defense` entries.
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
<!-- source: agent_runner/defenses.py catalog() -->
| Defense | Codifies | Guarded by |
|---|---|---|
| `round_timeout_s` | R1128 — TaskOutput polling loop 60min, scheduler grace fails to trigger | `tests/unit/test_agent_runtime.py` |
| `process_group_isolation` | #307 — process group reaping for descendant cleanup | `tests/unit/test_agent_runtime.py` |
| `sigterm_reaper` | R725 — SIGTERM-during-round dual-claude race | `tests/integration/test_serve_loop.py` |
| `orphan_stash_idempotency_s` | R820 — same-second 3 phantom stashes | `tests/unit/test_vcs_state.py` |
| `sha_locked_stash` | §9 IMMUTABLE — batch drop by index breaks under concurrent stash | `tests/invariants/test_stash_uses_sha_not_index.py` |
| `set_diff_classification` | R2110 — rotation-only diff via +-line scan misclassifies | `tests/invariants/test_set_diff_for_auto_tool_classification.py` |
| `critical_envs_injection` | Env injection via [agent.env] block — preset-supplied per CLI (e.g. DISABLE_AUTOUPDATER for claude prevents mid-loop self-updates) | `tests/unit/test_agent_runtime.py` |
| `startup_smoke_check` | R721 + #446 — _common.md frontmatter caused 4h/123-round silent burn; now halts serve (config_broken) instead of respawning a broken config | `tests/unit/test_serve_config_broken.py` |
| `crash_loop_breaker` | Run 6 — crashing agent respawned ~100 empty rounds at a fixed 2x delay | `tests/unit/test_serve_crash_loop.py` |
| `mem_loop_breaker` | 0.2.15 — a host stuck under sustained memory pressure could mem-terminate every round forever with no give-up; break-then-restart cap added (exit 71 stays outside RestartPreventExitStatus, so systemd restarts serve, which may find the pressure has cleared) | `tests/unit/test_serve_crash_loop.py` |
| `bulk_round_log_prune_guard` | 0.2.4 — rounds/ pruning shipped against backlogs it never built; one deployment's first post-upgrade round would have deleted 12,193 of 12,293 transcripts silently | `tests/unit/test_round_log_helpers.py` |
| `flock_concurrency` | Architectural — prevent concurrent supervisors corrupting state | `tests/unit/test_runner.py` |
| `atomic_state_writes` | Data integrity — crashes never leave half-written state files | `tests/invariants/test_atomic_write_enforced.py` |
| `event_kind_registry` | Prevent events.emit() typos / unregistered kinds slipping past CI | `tests/invariants/test_event_kind_registry.py` |
<!-- /gen:defenses-table -->

## Monitor: 13 detectors

Three categories by `auto_action`:

**Notify only** (`auto_action="none"`; severity `warning`, except `mem_pressure`
which can also report `critical`):
`timeout_rate`, `hung`, `orphan_chain`, `disk_warning`, `mem_pressure`,
`mem_pressure_gate_inert`, `mem_signal_unavailable`, `network_fail`,
`rate_limit_active`, `anomaly_repetitive_active`, `supervisor_stale`.

**Auto-stop service** (severity `critical`, `auto_action="stop_service"`):
`oauth_fail`, `disk_critical`. Continuing in either state is harmful (burning
API quota / writing to a near-full disk).

`mem_pressure`'s own `auto_action` stays `"none"` — the graded, plugin-
configurable admission lever through `on_alert` is 0.3. The actual
coma-preventer is a separate, serve-local admission gate
(`agent_runner/host_health.py` + `cli/serve_cmd.py`), independent of the
monitor's `auto_action`: before starting a round the loop samples
`host_health` and **defers** while it reports pressure (`round_deferred` /
`round_resumed`, mirroring `schedule_paused`/`resumed`); while a round is in
flight it resamples every ~10s and, on `critical` pressure, **terminates**
the round (`round_mem_terminated`).

<!-- gen:detector-list -->
<!-- source: agent_runner/_monitor_registry.py KNOWN_ALERT_KINDS / AUTO_STOP_ALERTS -->
- `anomaly_repetitive_active`
- `disk_critical` — **auto-stop**
- `disk_warning`
- `hung`
- `mem_pressure`
- `mem_pressure_gate_inert`
- `mem_signal_unavailable`
- `network_fail`
- `oauth_fail` — **auto-stop**
- `orphan_chain`
- `rate_limit_active`
- `supervisor_stale`
- `timeout_rate`
<!-- /gen:detector-list -->

## Monitor: anomaly-only by design

The monitor emits no events during healthy operation — it surfaces alerts only when a detector fires. To verify the monitor process is running, look for the `monitor_started` event in `events-*.jsonl`. Programmatic consumers (e.g. an external supervisory layer) should subscribe to that event kind as the canonical "supervision is up" signal. The event carries `mode: "anomaly-only"` to document the intentional silence.

## Remote observation: relay, not remote detection

Detection is on-host by design. The detectors read the supervised host's round
logs and metrics, and `auto_stop_on` stops that host's service — so detection
and auto-stop survive a closed laptop, a dead VPN, or a dropped ssh session
with no client in the loop. `monitor --host` is therefore rejected for
`--mode anomaly | narrate | http`.

The one remote mode is an event **relay**: `monitor --host X --mode events`
(`agent_runner/remote_relay.py`) runs on the client, spawns
`ssh X -- agent-runner events --tail --kind …`, and passes the remote's JSONL
through unmodified. It is a transport, and carries no detector logic — the
reconnect, give-up and process-group mechanics live in
`docs/runbook.md` § "Remote event relay & SSH trust".

## Plugin injection: two paths

agent-runner has TWO independent mechanisms for plugins to influence the agent's prompt.
Operators sometimes conflate them. The flags are independent.

### Path 1: round-context.json prepend (controlled by `[prompt] inject_context`)

Before each round, the supervisor writes `round-context.json` to `{log_dir}/round-context.json`
with phase, round_num, plugin-provided context fields (from ContextEnricher), and
recent_events tail. If `[prompt] inject_context = true` (default), this JSON is prepended <!-- authored: documents the shipped inject_context default; SSOT agent_runner/config/models.py -->
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

## Dirty-handler seam (0.2.0+)

After a round exits cleanly with a dirty working tree, the runner dispatches a
priority-ordered chain of registered `DirtyHandler` plugins. The first to return
a non-`None` `DirtyOutcome` wins; remaining handlers are skipped.

This makes dirty-tree policy fully pluggable. The `default_dirty_handler` plugin
(bundled, default-on, priority 1000) implements the `stash` / `ignore` /
`auto_commit` policy from `[vcs] dirty_action` — so the external behavior is
identical to pre-0.2.0 unless a consumer plugin intervenes first.

**Lifecycle-hook family** (5 groups, run in this order per round):

1. `serve_startup_hooks` — once at `agent-runner serve` boot, before the loop
2. `pre_round_hooks` — after lock acquire, before context is written
3. `context_enrichers` — pre-round, their slices merged into round-context.json
4. `dirty_handler_hooks` — after the agent exits, if the tree is dirty on a clean exit
5. `post_round_hooks` — last, after the `round_end` event is emitted

The stash *mechanism* (SHA lock, idempotency guard, `stash_orphan` /
`try_auto_commit` primitives) stays in core. The stash *policy* is plugin-provided.

See `docs/plugins.md` for the `DirtyHandler` protocol and override recipe.

## Known event kinds

<!-- gen:event-kinds -->
<!-- source: agent_runner/events.py KNOWN_EVENT_KINDS -->
- `agent_auth_error_detected`
- `agent_exit`
- `agent_network_blip`
- `agent_self_terminated`
- `agent_spawn`
- `agent_usage_recorded`
- `anomaly_repetitive_tool`
- `config_broken`
- `config_migrated`
- `crash_loop`
- `detector_error`
- `dirty_auto_committed`
- `dirty_check_failed`
- `dirty_commit_failed`
- `dirty_detected`
- `fresh_eyes_round_triggered`
- `hook_failed`
- `host_cgroup_memory_limit`
- `max_rounds_reached`
- `mem_loop`
- `mem_pressure_deferred_to_cgroup`
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
- `round_deferred`
- `round_end`
- `round_grace_extended`
- `round_grace_kill`
- `round_logs_prune_deferred`
- `round_mem_critical_sample`
- `round_mem_terminated`
- `round_progress`
- `round_resumed`
- `round_start`
- `round_substrate_after`
- `round_substrate_before`
- `round_supervisor_wedged`
- `round_timeout_kill`
- `schedule_paused`
- `schedule_phase_skipped`
- `schedule_resumed`
- `serve_startup_hook_failed`
- `service_upgrade_rollback_failed`
- `service_upgrade_rolled_back`
- `service_upgraded`
- `smoke_check_failed`
- `stale_index_lock_cleared`
- `status_recovered`
- `stop_file_detected`
- `transient_error_backoff_capped`
- `transient_error_detected`
- `transient_error_recovered`
- `upgrade_start_failed`
<!-- /gen:event-kinds -->

## 中文摘要

三层架构：Round（一轮 agent）/ Loop（serve 薄壳）/ Witness（monitor）。
三视角对称：peek（快照）/ watch（快照循环）/ monitor（异常检测），共用下钻参数。
防御以结构化目录形式存在（14 条），每条防御自描述「防的是哪条历史教训、被哪个 invariant test 守、当前状态」。
