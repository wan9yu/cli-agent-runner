# Configuration reference

`agent-runner.toml` lives in your project's working directory. `agent-runner init`
writes a templated copy you can edit.

## TOML schema

<!-- gen:config-schema -->
### `[agent]`

| Field | Type | Default |
|---|---|---|
| `command` | `list[str]` | — |
| `prompt_arg_template` | `list[str]` | — |

### `[runtime]`

| Field | Type | Default |
|---|---|---|
| `work_dir` | `Path` | — |
| `log_dir` | `Path` | — |
| `round_timeout_s` | `int` | 1800 |
| `restart_delay_s` | `int` | 3 |

### `[prompt]`

| Field | Type | Default |
|---|---|---|
| `file` | `Path` | — |
| `inject_context` | `bool` | True |

### `[vcs]`

| Field | Type | Default |
|---|---|---|
| `orphan_action` | `str` | 'stash' |
| `stash_idempotency_s` | `int` | 5 |
<!-- /gen:config-schema -->

## `[phases]` (optional)

| Field | Type | Default | Notes |
|---|---|---|---|
| `list` | list[str] | (none → no phase rotation) | round N gets `phases[(N-1) % len(phases)]` |

## `[monitor]` (optional, defaults shown)

```toml
[monitor]
auto_stop_on = ["oauth_fail", "disk_critical"]
disk_warning_pct = 90.0
disk_critical_pct = 95.0
oauth_fail_threshold = 2     # number of last-10 rounds matching auth pattern before auto-stop
```

Comment out individual entries to disable; e.g. `# auto_stop_on = []` disables
all auto-stop behaviour and reduces monitor to alert-only.

## `[llm]` (Phase 3 — reserved, not yet used)

```toml
# [llm]
# endpoint = "anthropic"
# api_key_env = "ANTHROPIC_API_KEY"
# base_url = "https://api.anthropic.com"
# model = "claude-haiku-4-5"
# enabled_for = []
```

Phase 2 leaves the `[llm]` section commented out as a forward-compatibility
hook. Phase 3 will introduce LLM-augmented commands; current builds ignore the
section if you uncomment it.

## 中文摘要

主要小节：`[agent]` 命令模板、`[runtime]` 工作目录与日志目录、`[prompt]` 提示词位置、
`[phases]` 可选阶段轮转、`[vcs]` stash 控制、`[monitor]` 可选自动停服策略、
`[llm]` 留给 Phase 3 的占位段。
