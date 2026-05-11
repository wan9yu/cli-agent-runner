# Architecture

## Three-layer model

```
┌──────────────────────────────────────────┐
│ Layer 4: The Critic (Phase 3, reserved)  │  LLM/invariant feedback loop
├──────────────────────────────────────────┤
│ Layer 3: The Witness (Phase 2)           │  agent-runner monitor
├──────────────────────────────────────────┤
│ Layer 2: The Loop (Phase 1+2)            │  agent-runner serve (≤60 LOC)
├──────────────────────────────────────────┤
│ Layer 1: The Round (Phase 1)             │  agent-runner round
└──────────────────────────────────────────┘
```

Each layer can run without the layer above. The Witness can watch a remote
Loop. Phase 3 Critic will analyse multiple Loops cross-project.

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

The catalog is the **single source of truth**. `peek`, `status`, the start
banner, and (Phase 3) the LLM Critic all read it. Adding a new defense = one
entry here + automatic surfacing everywhere.

## Phase 1 defense roster (11 entries)

| Defense | Codifies | Guarded by |
|---|---|---|
| `round_timeout_s` | R1128 (TaskOutput poll loop) | `tests/invariants/test_round_timeout_is_hard_wall.py` |
| `process_group_isolation` | #307 | `tests/unit/test_agent_runtime.py` |
| `sigterm_reaper` | R725 (dual-claude race) | — |
| `orphan_stash_idempotency_s` | R820 (3 phantom stashes/sec) | `tests/invariants/test_orphan_stash_idempotency.py` |
| `sha_locked_stash` | §9 IMMUTABLE | `tests/invariants/test_stash_uses_sha_not_index.py` |
| `set_diff_classification` | R2110 | `tests/invariants/test_set_diff_for_auto_tool_classification.py` |
| `critical_envs_injection` | autoupdater + effort | `tests/invariants/test_agent_subprocess_injects_critical_envs.py` |
| `startup_smoke_check` | R721 + #446 (4h silent burn) | `tests/invariants/test_prompt_smoke_check_contracts.py` |
| `flock_concurrency` | (Phase 1 design) | — |
| `atomic_state_writes` | data integrity | `tests/invariants/test_atomic_write_enforced.py` |
| `event_kind_registry` | typo prevention | `tests/invariants/test_event_kind_registry.py` |

## Monitor: 9 detectors

Three categories by `auto_action`:

**Notify only** (severity `warning`):
`timeout_rate`, `hung`, `orphan_chain`, `disk_warning`, `mem_pressure`,
`smoke_fail_rate`, `network_fail`.

**Auto-stop service** (severity `critical`, `auto_action="stop_service"`):
`oauth_fail`, `disk_critical`. Continuing in either state is harmful (burning
API quota / writing to a near-full disk).

## Phase 3 hooks (reserved, not implemented)

- `[llm]` config block in `agent-runner.toml`
- `agent_runner.critic` — `Critic` and `Finding` Protocol stubs

When Phase 3 lands, concrete Critics will analyse `ProjectState` snapshots and
emit `Finding` objects. The current architecture leaves the seam open without
committing to an implementation.

## 中文摘要

四层架构：Round（一轮 agent）/ Loop（serve 薄壳）/ Witness（monitor）/ Critic（Phase 3 反思层）。
三视角对称：peek（快照）/ watch（快照循环）/ monitor（异常检测），共用下钻参数。
防御以结构化目录形式存在（11 条），每条防御自描述「防的是哪条 argus 教训、被哪个 invariant test 守、当前状态」。
Phase 3 占位：`[llm]` 配置段 + `Critic` Protocol，留接口不实现。
