# Configuration reference

`agent-runner.toml` lives in your project's working directory. `agent-runner init`
writes a templated copy you can edit.

## Config reload

`agent-runner.toml` changes do NOT take effect mid-round. The supervisor
reads the TOML once at startup and reuses the loaded `Config` for every
round. To pick up a TOML change:

```bash
agent-runner restart
```

This is intentional: changing config mid-round would tear semantics (e.g.
a round dispatched with `dirty_action = "stash"` but committing while
running with newly-set `dirty_action = "auto_commit"` is undefined).

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
| `disable_pre_round_hooks` | `bool` | False |
| `round_log_retention` | `int` | 100 |
| `narrative_file` | `Path | None` | None |
| `transient_error_action` | `Literal['back_off', 'skip', 'stop']` | 'back_off' |
| `max_rounds` | `int | None` | None |
| `stop_file` | `Path | None` | None |
| `substrate_fingerprint_paths` | `list[str]` | [] |
| `fresh_eyes_every_n` | `int | None` | None |
| `dry_run` | `bool` | False |
| `max_grace_after_result_s` | `int` | 0 |
| `grace_kill_ignore_patterns` | `list[str]` | [] |

### `[prompt]`

| Field | Type | Default |
|---|---|---|
| `file` | `Path | None` | None |
| `files` | `list[Path]` | [] |
| `inject_context` | `bool` | True |
| `context_injection_mode` | `Literal['prepend', 'file', 'none']` | 'prepend' |
| `concat_separator` | `str` | '

' |
| `strip_yaml_frontmatter` | `bool` | True |

### `[vcs]`

| Field | Type | Default |
|---|---|---|
| `stash_idempotency_s` | `int` | 5 |
| `dirty_action` | `Literal['stash', 'ignore', 'auto_commit']` | 'stash' |

### `[monitor]`

| Field | Type | Default |
|---|---|---|
| `auth_fail_patterns` | `list[str]` | ['\b(oauth|unauthorized|401|api[_ ]key|auth(entication)?[_ -]?(failed|error|expired)|session.*expired)\b'] |
| `auth_fail_hint` | `str` | '' |
| `auto_stop_on` | `list[str]` | ['oauth_fail', 'disk_critical'] |
| `remote_failure_tolerance_s` | `int` | 90 |
| `anomaly_repetitive_window` | `int` | 0 |
| `anomaly_repetitive_threshold` | `int` | 0 |
| `host_health` | `MonitorHostHealthConfig` | MonitorHostHealthConfig(mem_avail_min_mb=200, disk_warning_pct=90.0, disk_critical_pct=95.0) |
| `round_progress_interval_s` | `int` | 0 |
| `supervisor_stale_threshold_s` | `int | None` | None |
<!-- /gen:config-schema -->

### `vcs.dirty_action`

Type: string, one of `"stash"`, `"ignore"`, `"auto_commit"`
Default: `"stash"`

Controls supervisor behavior when round subprocess exits with a dirty
working tree:

| Value | Behavior |
|---|---|
| `"stash"` | Auto-stash dirty tree with ORPHAN-prefix message. `dirty_detected` + `orphan_stashed` events emitted. |
| `"ignore"` | Emit `dirty_detected` event only. Working tree left dirty for next round. |
| `"auto_commit"` | Supervisor commits with subject `agent-runner auto-commit: R<N> <phase>`. No push. On failure, emits `dirty_commit_failed`, leaves tree dirty. |

## `[agent.env]` (optional)

`[agent.env]` is a flat `dict[str, str]` of environment variables injected into
the agent subprocess **per round**. This is preset-supplied per CLI: e.g. the
claude preset sets `DISABLE_AUTOUPDATER=1` to prevent mid-loop self-updates;
the aider and codewhale presets omit `[agent.env]` entirely (both resolve their
API keys from the ambient environment or their own keyrings). Override these
values in your project's `agent-runner.toml` only when you need to deviate from
the preset default. The runtime merges `[agent.env]` on top of the supervisor's
own env; unset (empty string) does not unset an inherited variable.

## `[monitor].auth_fail_hint` (preset-supplied)

The TOML schema default for `auth_fail_hint` is `""` — that's the "no-hint"
sentinel. **Presets supply a per-CLI hint** so operators get actionable
guidance without authoring it themselves:

- `--preset claude` → recommend `claude /login` / refresh `ANTHROPIC_API_KEY`.
- `--preset aider` → verify provider env var (`OPENAI_API_KEY` /
  `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / etc.); run `aider --models`.
- `--preset codewhale` → run `codewhale auth status` to inspect provider
  credentials, or set `DEEPSEEK_API_KEY` on the supervisor host.

Override in your `agent-runner.toml` if you ship a custom CLI.

## `[prompt]` multi-file concat (0.1.16+)

Use `prompt.files` to assemble the round prompt from multiple Markdown files
(e.g. a shared preamble + a role-specific body):

```toml
[prompt]
files = ["_common.md", "dev.md"]
concat_separator = "\n\n"        # default; use "\n\n---\n\n" for visible breaks
strip_yaml_frontmatter = true    # default; set false for non-LLM-CLI agents
```

- **Missing `files[0]`** → `ConfigError` (fail-fast; the first file is required).
- **Missing `files[n≥1]`** → warning logged, file skipped (supports optional preamble pattern).
- **Both `prompt.file` and `prompt.files` set** → `ConfigError`.
- **`prompt.file = "x.md"` shorthand** — single-file back-compat, still works unchanged.
- **`strip_yaml_frontmatter`** — YAML frontmatter on the first file is stripped before passing to the agent (R721 defense against `claude -p '---...'` arg-parse rejection). Set `false` to preserve frontmatter for agents that parse it themselves.

Paths are resolved against `runtime.work_dir` (consistent with existing path resolution).

## `[phases]` (optional)

| Field | Type | Default | Notes |
|---|---|---|---|
| `list` | list[str] | (none → no phase rotation) | round N gets `phases[(N-1) % len(phases)]` |

> **Manual override**: pass `--phase NAME` to `agent-runner round` to bypass
> the rotation counter (audit, debug, multi-script orchestration). The internal
> counter is unaffected — subsequent default rounds resume rotation. The name
> must match one of the entries in `[phases].list`.

> **Phase rotation indexing**: `phase = phases.list[round_num % len(phases.list)]`.
> When `round_num` doesn't start at 0 (e.g. resuming after a restart with an
> existing round counter, or continuing into round 477), rotation appears to
> "start" at `phases.list[round_num % len]`, not `phases.list[0]`. This is by
> design (rotation is deterministic on round_num). If you need a specific
> starting phase, ensure the starting `round_num` matches.

## `[phases.<name>]` per-phase sub-tables (0.1.16+)

Each phase can carry its own overrides for up to three fields. The phase name
must appear in `phases.list` (typo catcher); unknown fields are rejected at
config load.

**Whitelisted per-phase fields:**

| Field | Type | Overrides |
|---|---|---|
| `round_timeout_s` | `int` | `runtime.round_timeout_s` |
| `disable_pre_round_hooks` | `bool` | `runtime.disable_pre_round_hooks` |
| `prompt.files` | `list[str]` | `prompt.files` |

```toml
[runtime]
round_timeout_s = 1800           # fallback for unconfigured phases

[phases]
list = ["dev", "qa", "product"]

[phases.dev]
round_timeout_s = 3600           # implementation work, longer budget
prompt.files = ["_common.md", "dev.md"]

[phases.qa]
round_timeout_s = 1200           # test review, tighter budget
disable_pre_round_hooks = true   # audit phase: no hook pollution
prompt.files = ["_common.md", "qa.md"]

[phases.product]
round_timeout_s = 1200           # docs writing, tighter budget
prompt.files = ["_common.md", "product.md"]
```

Unconfigured phases (and configs without `[phases]`) keep using the global
`runtime.round_timeout_s`.

> **Migration from 0.1.15**: `runtime.round_timeout_per_phase` dict syntax is
> removed. See `docs/migrations/0.1.16.md` for the full recipe.

## `[monitor]` (optional, defaults shown)

> Authoritative field-level defaults are in the generated schema table above
> (`[monitor]` section). The snippet below shows only the fields most commonly
> customised, with operational notes.

```toml
[monitor]
auto_stop_on = ["oauth_fail", "disk_critical"]
round_progress_interval_s = 0  # 0 = disabled; set >0 to emit round_progress heartbeat events
# supervisor_stale_threshold_s = 2700  # unset = round_timeout_s * 1.5; 0 = disable

[monitor.host_health]
# Thresholds for mem_pressure / disk_warning / disk_critical. Defaults are
# authoritative in the config-schema table above — set a field here only to
# override. (mem_avail_min_mb: mem_pressure when mem_available_mb below it;
# disk_warning_pct / disk_critical_pct: fire when disk_used_pct at/above.)
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
