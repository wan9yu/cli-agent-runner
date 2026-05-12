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
| `name` | `str | None` | None |

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
| `context_injection_mode` | `Literal['prepend', 'file', 'none']` | 'prepend' |

### `[vcs]`

| Field | Type | Default |
|---|---|---|
| `orphan_action` | `str` | 'stash' |
| `stash_idempotency_s` | `int` | 5 |

### `[monitor]`

| Field | Type | Default |
|---|---|---|
| `auth_fail_patterns` | `list[str]` | ['\b(oauth|unauthorized|401|api[_ ]key|auth(entication)?[_ -]?(failed|error|expired)|session.*expired)\b'] |
| `auth_fail_hint` | `str` | 'Run `claude /login` on the supervisor host or refresh ANTHROPIC_API_KEY' |
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

## `[llm]` (reserved, not yet used)

```toml
# [llm]
# endpoint = "anthropic"
# api_key_env = "ANTHROPIC_API_KEY"
# base_url = "https://api.anthropic.com"
# model = "claude-haiku-4-5"
# enabled_for = []
```

The `[llm]` section is a forward-compatibility placeholder; current builds
ignore it if you uncomment.

## Context injection modes

`prompt.context_injection_mode` controls how each round's context (round number,
phase, orphan stash info, etc.) is delivered to the agent:

- `prepend` (default): wraps the context as a fenced `json round-context` markdown block
  and prepends to the prompt. The agent reads it as the first thing in its input.
- `file`: skips the prepend; the supervisor still writes `round-context.json` into
  `runtime.log_dir` so the agent can read it explicitly. Useful for non-claude CLIs
  whose argv treatment differs.
- `none`: skips both the prepend and any built-in injection. Plugins (0.1.3+) or the
  agent itself handle context delivery. No backward-compat path — opt-in only.

`prompt.inject_context = false` overrides all modes (skips injection entirely).

## Monitor pattern overrides

`monitor.auth_fail_patterns` and `monitor.auth_fail_hint` let you tune the OAuth-fail
detector for non-claude providers. Defaults match claude's auth error vocabulary
(`401`, `unauthorized`, `oauth`, etc.) and recommend `claude /login`. To customize
for, say, an OpenAI-CLI agent:

```toml
[monitor]
auth_fail_patterns = [
    "\\b(invalid_api_key|incorrect_api_key|401)\\b",
]
auth_fail_hint = "Check OPENAI_API_KEY env var or rotate at platform.openai.com"
```
<!-- skip-test -->

## 中文摘要

主要小节：`[agent]` 命令模板、`[runtime]` 工作目录与日志目录、`[prompt]` 提示词位置、
`[phases]` 可选阶段轮转、`[vcs]` stash 控制、`[monitor]` 可选自动停服策略、
`[llm]` 占位段（暂不解析）。
