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
| `env` | `dict[str, str]` | {} |

### `[runtime]`

| Field | Type | Default |
|---|---|---|
| `work_dir` | `Path` | — |
| `log_dir` | `Path` | — |
| `round_timeout_s` | `int` | 1800 |
| `restart_delay_s` | `int` | 3 |
| `round_timeout_per_phase` | `dict[str, int]` | {} |
| `disable_pre_round_hooks` | `bool` | False |

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
| `auth_fail_hint` | `str` | '' |
| `auto_stop_on` | `list[str]` | ['oauth_fail', 'disk_critical'] |
| `remote_failure_tolerance_s` | `int` | 90 |
<!-- /gen:config-schema -->

## `[agent.env]` (optional)

`[agent.env]` is a flat `dict[str, str]` of environment variables injected into
the agent subprocess **per round**. This is preset-supplied per CLI: e.g. the
claude preset sets `DISABLE_AUTOUPDATER=1` to prevent mid-loop self-updates;
the aider preset omits `[agent.env]` entirely. Override these values in your
project's `agent-runner.toml` only when you need to deviate from the preset
default. The runtime merges `[agent.env]` on top of the supervisor's own env;
unset (empty string) does not unset an inherited variable.

## `[monitor].auth_fail_hint` (preset-supplied)

The TOML schema default for `auth_fail_hint` is `""` — that's the "no-hint"
sentinel. **Presets supply a per-CLI hint** so operators get actionable
guidance without authoring it themselves:

- `--preset claude` → recommend `claude /login` / refresh `ANTHROPIC_API_KEY`.
- `--preset aider` → verify provider env var (`OPENAI_API_KEY` /
  `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / etc.); run `aider --models`.

Override in your `agent-runner.toml` if you ship a custom CLI.

## `[phases]` (optional)

| Field | Type | Default | Notes |
|---|---|---|---|
| `list` | list[str] | (none → no phase rotation) | round N gets `phases[(N-1) % len(phases)]` |

> **Manual override**: pass `--phase NAME` to `agent-runner round` to bypass
> the rotation counter (audit, debug, multi-script orchestration). The internal
> counter is unaffected — subsequent default rounds resume rotation. The name
> must match one of the entries in `[phases].list`.

## Per-phase timeouts (0.1.9+)

If your `[phases]` rotation has phases with different wall-clock budgets,
override the global timeout per phase:

```toml
[runtime]
round_timeout_s = 1800           # fallback for unconfigured phases

[runtime.round_timeout_per_phase]
dev = 3600                       # implementation work, longer budget
qa = 1200                        # test review, tighter budget
product = 1200                   # docs writing, tighter budget

[phases]
list = ["dev", "qa", "product"]
```

Validation: typos in phase names (keys not in `[phases] list`) and
non-positive / non-integer values are caught at config-load time with
`ValueError`.

Unconfigured phases (and configs without `[phases]`) keep using the
global `round_timeout_s`.

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

## Context injection modes

`prompt.context_injection_mode` controls how each round's context (round number,
phase, orphan stash info, etc.) is delivered to the agent:

- `prepend` (default): wraps the context as a fenced `json round-context` markdown block
  and prepends to the prompt. The agent reads it as the first thing in its input.
- `file`: skips the prepend; the supervisor still writes `round-context.json` into
  `runtime.log_dir` so the agent can read it explicitly. Useful for CLIs whose
  argv treatment differs from claude's stdin-style flow.
- `none`: skips both the prepend and any built-in injection. Plugins (0.1.3+) or the
  agent itself handle context delivery. No backward-compat path — opt-in only.

`prompt.inject_context = false` overrides all modes (skips injection entirely).

## Monitor pattern overrides

`monitor.auth_fail_patterns` and `monitor.auth_fail_hint` let you tune the OAuth-fail
detector per agent CLI. The default `auth_fail_patterns` regex is broad
(`401`, `unauthorized`, `oauth`, generic `auth*_failed/error/expired`, expired
sessions) and matches most providers' auth error vocabulary; the
`auth_fail_hint` default is `""`, with presets supplying the per-CLI text
(`--preset claude` recommends `claude /login`, `--preset aider` points at the
provider env vars). To customize further — say, narrowing patterns for an
OpenAI-CLI agent:

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
`[phases]` 可选阶段轮转、`[vcs]` stash 控制、`[monitor]` 可选自动停服策略。
