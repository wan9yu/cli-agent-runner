# Configuration reference

`agent-runner.toml` lives in your project's working directory. `agent-runner init`
writes a templated copy you can edit.

## `[agent]`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `command` | list[str] | yes | — | argv to spawn the agent process |
| `prompt_arg_template` | list[str] | yes | — | how to inject the prompt; `{prompt}` is substituted |

## `[runtime]`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `work_dir` | str (path) | yes | — | git repo to operate in (`.` = cwd) |
| `log_dir` | str (path) | yes | — | state + logs; `{project}` substituted |
| `round_timeout_s` | int | no | 1800 | wall-clock kill threshold |
| `restart_delay_s` | int | no | 3 | sleep between rounds in `serve` |

## `[prompt]`

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `file` | str (path) | yes | — | the .md prompt to feed the agent each round |
| `inject_context` | bool | no | true | prepend round-context JSON block above the prompt |

## `[phases]` (optional)

| Field | Type | Default | Notes |
|---|---|---|---|
| `list` | list[str] | (none → no phase rotation) | round N gets `phases[(N-1) % len(phases)]` |

## `[vcs]`

| Field | Type | Default | Notes |
|---|---|---|---|
| `orphan_action` | str | "stash" | only "stash" supported in Phase 1+2 |
| `stash_idempotency_s` | int | 5 | window inside which duplicate stashes are deduped |

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
