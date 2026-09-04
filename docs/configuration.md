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
<!-- source: agent_runner/config/models.py dataclasses -->
### `[agent]`

| Field | Type | Default |
|---|---|---|
| `command` | `list[str]` | — |
| `prompt_arg_template` | `list[str]` | — |
| `name` | `str \| None` | None |
| `env` | `dict[str, str]` | {} |
| `prompt_delivery` | `Literal['argv', 'stdin']` | 'argv' |

### `[runtime]`

| Field | Type | Default |
|---|---|---|
| `work_dir` | `Path` | — |
| `log_dir` | `Path` | — |
| `round_timeout_s` | `int` | 1800 |
| `restart_delay_s` | `int` | 3 |
| `disable_pre_round_hooks` | `bool` | False |
| `round_log_retention` | `int` | 0 |
| `narrative_file` | `Path \| None` | None |
| `transient_error_action` | `Literal['back_off', 'skip', 'stop']` | 'back_off' |
| `max_rounds` | `int \| None` | None |
| `stop_file` | `Path \| None` | None |
| `substrate_fingerprint_paths` | `list[str]` | [] |
| `fresh_eyes_every_n` | `int \| None` | None |
| `dry_run` | `bool` | False |
| `max_grace_after_result_s` | `int` | 0 |
| `grace_kill_ignore_patterns` | `list[str]` | [] |

### `[prompt]`

| Field | Type | Default |
|---|---|---|
| `file` | `Path \| None` | None |
| `files` | `list[Path]` | [] |
| `inject_context` | `bool` | True |
| `context_injection_mode` | `Literal['prepend', 'file', 'none']` | 'prepend' |
| `concat_separator` | `str` | '\n\n' |
| `strip_yaml_frontmatter` | `bool` | True |

### `[vcs]`

| Field | Type | Default |
|---|---|---|
| `stash_idempotency_s` | `int` | 5 |
| `dirty_action` | `Literal['stash', 'ignore', 'auto_commit']` | 'stash' |

### `[monitor]`

| Field | Type | Default |
|---|---|---|
| `auth_fail_patterns` | `list[str]` | ['\\b(oauth\|unauthorized\|401\|api[_ ]key\|auth(entication)?[_ -]?(failed\|error\|expired)\|session.*expired)\\b'] |
| `auth_fail_hint` | `str` | '' |
| `auto_stop_on` | `list[str]` | ['oauth_fail', 'disk_critical'] |
| `remote_failure_tolerance_s` | `int` | 90 |
| `anomaly_repetitive_window` | `int` | 0 |
| `anomaly_repetitive_threshold` | `int` | 0 |
| `host_health` | `MonitorHostHealthConfig` | MonitorHostHealthConfig(mem_avail_min_mb=200, disk_warning_pct=90.0, disk_critical_pct=95.0, swap_sout_noise_floor_mb=32, mem_free_low_mb=16) |
| `round_progress_interval_s` | `int` | 0 |
| `supervisor_stale_threshold_s` | `int \| None` | None |

#### `[monitor.host_health]`

| Field | Type | Default |
|---|---|---|
| `mem_avail_min_mb` | `int` | 200 |
| `disk_warning_pct` | `float` | 90.0 |
| `disk_critical_pct` | `float` | 95.0 |
| `swap_sout_noise_floor_mb` | `int` | 32 |
| `mem_free_low_mb` | `int` | 16 |

### `[phases]`

| Field | Type | Default |
|---|---|---|
| `list` | `list[str] \| None` | None |
| `overrides` | `dict[str, PhaseOverride]` | {} |
| `phase_policy` | `Literal['wait', 'skip']` | 'wait' |

### `[plugins]`

| Field | Type | Default |
|---|---|---|
| `disable` | `list[str]` | [] |
| `raw` | `dict[str, Any]` | {} |

### `[schedule]`

| Field | Type | Default |
|---|---|---|
| `timezone` | `str \| None` | None |
| `run_windows` | `tuple[schedule.Window, ...]` | () |
| `pause_windows` | `tuple[schedule.Window, ...]` | () |
<!-- /gen:config-schema -->

### `agent.prompt_delivery`

Type: string, one of `"argv"`, `"stdin"`

Controls how the assembled prompt reaches the agent subprocess. `"argv"`
(the original behavior) substitutes the prompt into
`prompt_arg_template` and passes it as a command-line argument. `"stdin"`
writes the prompt to the subprocess's stdin instead — it never appears in
argv, which avoids a `pkill -f <token>` self-kill if the agent's own cleanup
commands happen to match a token drawn from its prompt. The `claude` preset
defaults to `"stdin"`; existing configs are unchanged.

To adopt `"stdin"` on an existing config, two edits are required together —
`prompt_delivery = "stdin"` does **not** remove `{prompt}` for you, and load
rejects the combination of `"stdin"` with a `{prompt}` placeholder still present
(`ConfigError` at startup, since the prompt is piped to stdin, not placed in
argv):

```toml
[agent]
prompt_delivery = "stdin"
prompt_arg_template = ["-p"]   # remove {prompt} — no longer substituted into argv
```

### `runtime.round_log_retention`

**Pruning is opt-in. `0` — the default — never prunes anything.** Round logs
accumulate for as long as the deployment runs, and agent-runner deletes none of
them unless you ask it to. Must be `>= 0`; a negative value is rejected at
config load.

The knob governs both round-log families:

- the serve-level `{log_dir}/round-<N>.log` files, pruned by mtime once at
  serve startup;
- the agent transcripts in `{log_dir}/rounds/R<N>-<timestamp>.log`, pruned by
  round number at the start of every round.

**We are not ignoring disk.** Unbounded growth is watched, and loudly: the
`disk_warning` detector alerts at 90% used and `disk_critical` — in the default
`[monitor] auto_stop_on` — stops the service at 95%. Deleting history has no
equivalent defense; it is discovered on the day you need the file and it is not
there. Given a risk that already auto-stops the service versus a risk with no
detector at all, the default belongs on the side that keeps the files.

### Enabling pruning

Set a positive count — the number of files to keep per family:

```toml
[runtime]
round_log_retention = 100   # pre-0.2.6 behavior
```

Sizing it: agent transcripts are one file per round, from a few KB to several
MB each depending on how verbose the agent CLI is. Multiply by your rounds/day
and check it against free space rather than picking a round number.

### Bulk-prune guard (0.2.6+)

If you do enable pruning, a prune that would delete *more* files than it keeps
is a bulk prune, and a bulk prune deletes nothing. It emits
`round_logs_prune_deferred` (fields: `directory`, `existing`, `keep`,
`would_delete`, `hint`) and leaves every file in place. In steady state a round
retires about one file, so the guard only trips when you first opt in on an
existing backlog, or when you lower the value far below the current file
count — exactly the moments a mass deletion would otherwise happen silently.

The deferral is permanent until you act, and you have two options:

- **Raise `round_log_retention`** to at least the reported `existing` count.
  The backlog then fits inside the kept window, nothing is deleted, and normal
  per-round pruning resumes once the count grows past the new value.
- **Delete files yourself** from the reported `directory`. The next prune sees
  a count the guard no longer calls bulk and resumes.

The supervisor never performs a bulk deletion on its own. Note that `0` is not
a deferral: it emits nothing, because never-prune is a stated intent rather
than a backlog awaiting a decision.

### `vcs.dirty_action`

Type: string, one of `"stash"`, `"ignore"`, `"auto_commit"`

Controls supervisor behavior when round subprocess exits with a dirty
working tree. This config is read by the bundled `default_dirty_handler` plugin
(0.2.0+, default-on). Existing behavior and TOML are unchanged.

| Value | Behavior |
|---|---|
| `"stash"` | Auto-stash dirty tree with ORPHAN-prefix message. `dirty_detected` + `orphan_stashed` events emitted. |
| `"ignore"` | Emit `dirty_detected` event only. Working tree left dirty for next round. |
| `"auto_commit"` | Supervisor commits with subject `agent-runner auto-commit: R<N> <phase>`. No push. On success, emits `dirty_auto_committed`. On failure, emits `dirty_commit_failed`, leaves tree dirty. |

To replace this policy entirely, disable the bundled plugin and register your own
`DirtyHandler` — see `docs/plugins.md`.

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

<!-- authored: canonical auth_fail_hint sentinel; SSOT agent_runner/config/models.py -->
The TOML schema default for `auth_fail_hint` is `""` — that's the "no-hint"
sentinel. **Presets supply a per-CLI hint** so operators get actionable
guidance without authoring it themselves:

- `--preset claude` → recommend `claude /login` / refresh `ANTHROPIC_API_KEY`.
- `--preset aider` → verify provider env var (`OPENAI_API_KEY` /
  `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / etc.); run `aider --models`.
- `--preset codewhale` → run `codewhale auth status` to inspect provider
  credentials, or set `DEEPSEEK_API_KEY` on the supervisor host.
- `--preset gemini` → verify your API key or check your authentication status
  for Gemini CLI.
- `--preset kimi` → set the `KIMI_MODEL_*` env vars (see `recipes/kimi.md`), or
  run `kimi login`; `kimi doctor` validates config.
- `--preset pi` → configure a pi provider in `~/.pi/agent/models.json` (see
  `recipes/pi.md`) or run `pi /login`, and set `--model` to a reachable
  provider/model.

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

- **Missing `files[0]`** → `FileNotFoundError` when the round assembles the prompt (the first file is required; the existence check happens at prompt assembly, not at config load).
- **Missing `files[n≥1]`** → warning logged, file skipped (supports optional preamble pattern).
- **Both `prompt.file` and `prompt.files` set** → `ConfigError`.
- **`prompt.file = "x.md"` shorthand** — single-file back-compat, still works unchanged.
- **`strip_yaml_frontmatter`** — YAML frontmatter on the first file is stripped before passing to the agent (R721 defense against `claude -p '---...'` arg-parse rejection). Set `false` to preserve frontmatter for agents that parse it themselves.

Paths are resolved against `runtime.work_dir` (consistent with existing path resolution).

A relative `runtime.work_dir` itself resolves against the **config file's own directory**, not the caller's cwd — so `agent-runner serve --config /srv/proj/agent-runner.toml` behaves identically from any working directory.

## `[phases]` (optional)

Field-level types and defaults are in the generated schema table above
(`[phases]` section). Rotation is a pure function of the 1-based round
counter: round N runs `phases.list[(N - 1) % len(phases.list)]`. Unset
`list` means no rotation.

> **Manual override**: pass `--phase NAME` to `agent-runner round` to bypass
> the rotation counter (audit, debug, multi-script orchestration). The internal
> counter is unaffected — subsequent default rounds resume rotation. The name
> must match one of the entries in `[phases].list`.

> **Phase rotation indexing**: `phase = phases.list[(round_num - 1) % len(phases.list)]`.
> `round_num` is 1-based, so round 1 gets `phases.list[0]`. Resuming after a
> restart continues from the persisted counter rather than restarting the
> rotation — round N always maps to the same phase regardless of when it ran.
> This is by design (rotation is deterministic on `round_num`). If you need a
> specific starting phase, ensure the starting `round_num` matches.

## `[phases.<name>]` per-phase profiles (0.2.9+)

Each phase in `phases.list` can carry its own sub-tables that override the base
config for the rounds that run under it. The supervisor resolves a **profile**
per round — `(agent, runtime, schedule, prompt files)` — by starting from the
base `[agent]` / `[runtime]` / `[schedule]` / `[prompt]` and layering the
phase's overrides on top. A phase with no sub-tables runs the base config
unchanged, so existing configs are byte-for-byte unaffected.

The phase name must appear in `phases.list` (typo catcher); unknown top-level
per-phase fields and unknown sub-tables are rejected at config load. All four
sub-tables reject unknown keys: `agent` and `runtime` as noted below, and
`schedule` / `prompt` against their own known set (`schedule`: `timezone` /
`run_windows` / `pause_windows`; `prompt`: `files`).

### The four sub-tables

| Sub-table | Overrides | Fields accepted |
|---|---|---|
| `[phases.<name>.agent]` | `[agent]` | any `[agent]` field; field-merged onto the base `[agent]`, then validated (so the `stdin` + `{prompt}` cross-check runs on the merged result) |
| `[phases.<name>.runtime]` | `[runtime]` | `round_timeout_s`, `disable_pre_round_hooks` only |
| `[phases.<name>.schedule]` | `[schedule]` | any `[schedule]` field; **replaces** the global windows wholesale |
| `[phases.<name>.prompt]` | `[prompt]` | `files` only |

**`agent` merges; `schedule` replaces.** A per-phase `[...agent]` merges key by
key onto the base `[agent]` — unset fields inherit — so a phase can swap only
`command` and keep the shared `prompt_arg_template` and `env`. A per-phase
`[...schedule]` replaces the base windows entirely; set `pause_windows = []` to
make one phase run around the clock even while the global schedule pauses. A
per-phase schedule that omits `timezone` inherits the global one.

**Flat aliases.** `round_timeout_s`, `disable_pre_round_hooks`, and
`prompt.files` may also be written directly under `[phases.<name>]` (the
pre-0.2.9 form). They are permanent aliases for the matching `runtime` / `prompt`
sub-table fields. Setting both a flat field and its `[phases.<name>.runtime]`
twin is a config error — use one.

### Never overridable per phase

A profile only re-points the four surfaces above. Everything else is read from
the base config regardless of phase:

- **`runtime.work_dir` / `runtime.log_dir`** — one working tree and one log
  directory per deployment; a phase cannot relocate them. `[phases.<name>.runtime]`
  accepts only `round_timeout_s` and `disable_pre_round_hooks`; any other runtime
  key there is rejected at load.
- the rest of `[runtime]` (`restart_delay_s`, `round_log_retention`,
  `transient_error_action`, `max_rounds`, `stop_file`, …), and all of `[vcs]`,
  `[monitor]`, and `[plugins]` — global only.
- `[prompt]` fields other than `files` (`context_injection_mode`,
  `inject_context`, `concat_separator`, …).

### `phase_policy` — `wait` vs `skip`

When a phase carries its own `[...schedule]`, the rotation can land on a phase
whose window is closed. `[phases] phase_policy` chooses what serve does then
(the generated schema table above lists its value):

- **`wait`** — run only this round's rotation phase. If its window is closed,
  serve idle-sleeps until that phase's window opens, then runs it. The rotation
  never advances past a closed phase, so round N always maps to the same phase.
- **`skip`** — step forward through the rotation to the first phase whose window
  is open right now, and run that phase this round. The stepped-over phases emit
  `schedule_phase_skipped` (fields: `round_num`, `skipped`, `chosen`,
  `active_window`). If no phase is runnable, serve pauses as `wait` does.

`phase_policy` matters only alongside per-phase schedules: with a single global
`[schedule]` (or none) the rotation has nothing to skip and both values behave
identically. `agent-runner round` is never gated — a manual round runs its named
phase regardless of any window.

### Example: mixed-model rotation

Rotate three providers so each round runs under a different model, each phase
honoring its own provider's off-peak window. The base `[agent]` runs DeepSeek
via codewhale; the `glm` and `qwen` phases swap the whole `command` and inherit
the shared `prompt_arg_template`:

```toml
[agent]
command = ["codewhale", "exec", "--auto", "--output-format", "stream-json"]
prompt_arg_template = ["{prompt}"]

[runtime]
work_dir = "/srv/research"
log_dir = "logs"
round_timeout_s = 1800            # base budget for phases that set none

[phases]
list = ["deepseek", "glm", "qwen"]
phase_policy = "skip"             # a closed provider window yields to the next phase

[phases.deepseek.schedule]
timezone = "Asia/Shanghai"
pause_windows = ["Mon-Fri 09:00-18:00"]   # skip DeepSeek's peak-price hours

[phases.glm.agent]
command = ["glm-cli", "run"]      # different provider CLI; inherits prompt_arg_template

[phases.glm.runtime]
round_timeout_s = 3600            # GLM runs the heavier synthesis pass

[phases.glm.schedule]
pause_windows = []                # no off-peak constraint — always runnable

[phases.qwen.agent]
command = ["qwen-cli", "chat"]

[phases.qwen.prompt]
files = ["prompts/_common.md", "prompts/qwen.md"]
```

Under `phase_policy = "skip"`, a round that lands on `deepseek` during its
Mon–Fri peak window steps to `glm` (always open) and runs that instead, emitting
`schedule_phase_skipped`. Since 0.2.10 `skip` also steps over a phase whose
provider is currently **throttled** (rate-limited / erroring), not just one whose
window is shut. Since 0.2.11 the throttle is keyed on the **agent** (the binary
basename of `command[0]`, matching the detector's label), not the phase: every
phase sharing a throttled agent is stepped over together, so two phases both
running `claude` won't hammer a rate-limited key, while a phase on a healthy
provider keeps running. serve idle-pauses (waking at the earliest reset) only when
*every* candidate is throttled or window-closed. Under `wait`, the same round
idle-sleeps until DeepSeek's window reopens rather than advancing. A
`docs/runbook.md` ("Mixed-model rotation") recipe walks the operational side.

> **Migration from the pre-0.2.9 flat form**: flat `round_timeout_s` /
> `disable_pre_round_hooks` under `[phases.<name>]` still work as aliases;
> `agent-runner migrate` reports (it does not rewrite) the option to nest them
> under `[phases.<name>.runtime]`. See `docs/migrations/0.2.md`.

> **Migration from 0.1.15**: `runtime.round_timeout_per_phase` dict syntax is
> removed.

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
# swap_sout_noise_floor_mb = 32   # lower on a tiny host (e.g. 8 on a 512MB Pi)
# mem_free_low_mb = 16            # raise if a larger host comas above 16 MiB free
```

Comment out individual entries to disable; e.g. `# auto_stop_on = []` disables
all auto-stop behaviour and reduces monitor to alert-only.

`remote_failure_tolerance_s` is read on the **relay client** only: it is how
long `monitor --host <alias> --mode events` keeps reconnecting before it emits
`monitor_remote_giveup` and exits 1 (`0` disables reconnection). It has no
effect on a monitor watching its own host.

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
detector per agent CLI. The default `auth_fail_patterns` regex is broad <!-- authored: describes the default auth_fail_patterns regex; SSOT agent_runner/config/models.py -->
(`401`, `unauthorized`, `oauth`, generic `auth*_failed/error/expired`, expired
sessions) and matches most providers' auth error vocabulary; the
`auth_fail_hint` is empty out of the box (see the `[monitor].auth_fail_hint`
section above), with presets supplying the per-CLI text
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

## `[schedule]` time-window gating (0.2.7+)

`[schedule]` gates the serve loop against a wall clock: between rounds, the
supervisor decides whether to launch the next round now or idle-sleep until a
configured window opens. **The section is opt-in** — omit it (or leave both
window lists empty) and serve runs 7×24 exactly as before. This section is the
authoritative description of `pause_windows` / `run_windows`; the generated
schema table above lists the raw field types (windows are stored parsed, hence
the internal `tuple[schedule.Window, ...]` type — configure them as the string
list documented here).

```toml
[schedule]
timezone = "Asia/Shanghai"
pause_windows = ["Mon-Fri 09:00-12:00", "Mon-Fri 14:00-18:00"]
```

### Fields

| Field | Meaning |
|---|---|
| `timezone` | IANA zone name (e.g. `"Asia/Shanghai"`) the windows are evaluated in. Omit → the supervisor host's local time. An unknown zone is rejected at config load. |
| `pause_windows` | List of windows during which serve must **not** start a round. |
| `run_windows` | List of windows during which serve **may** start a round. Empty (the default) means "every time is runnable". <!-- authored: empty run_windows default; SSOT agent_runner/config/models.py --> |

Gating happens **only between rounds**. An in-flight round is never interrupted
by a window boundary — a round that starts at 08:59 runs to completion even
though a pause window opens at 09:00. `agent-runner round` (a manual, one-shot
round) is **never** gated; scheduling governs the `serve` supervisor loop only.

### Window syntax: `[WEEKDAYS ]HH:MM-HH:MM`

- 24-hour `HH:MM`. The end bound is **exclusive**: `09:00-12:00` covers 09:00
  through 11:59 and reopens at 12:00.
- `24:00` is accepted **only** as an end bound and means end-of-day, so
  `00:00-24:00` is the whole day.
- A window whose end is at or before its start **wraps past midnight**:
  `22:00-02:00` covers 22:00–23:59 and 00:00–01:59 the next morning. Start must
  not equal end (zero-length is rejected).

### Weekday prefix (optional)

A window may carry an optional weekday prefix, scoping it to particular days:

- Tokens `Mon Tue Wed Thu Fri Sat Sun`, case-insensitive.
- Range: `Mon-Fri` (ascending only — `Fri-Mon` is rejected).
- List: `Sat,Sun`.
- Combination: `Mon-Fri,Sun`.
- **No prefix means every day** (`09:00-18:00` applies Mon through Sun).

For a wrapping window, the early-morning tail is attributed to the window's
**start** day: `Fri 22:00-02:00` covers Friday evening and the small hours of
Saturday, but not Saturday evening.

### Evaluation rule

A given instant is runnable when it is **inside a run window AND NOT inside any
pause window**:

- With only `pause_windows` set (the common case), serve runs at all times
  except inside a pause window.
- With `run_windows` set, serve runs only inside a run window, minus any pause
  window that overlaps it. `run_windows` is fully supported for this
  forward-compatible use; the pause-based form above is the documented recipe
  for this release.

The `[schedule]` block above is the current DeepSeek off-peak policy: rounds
pause during the provider's Mon–Fri peak hours and run at all other times,
including the full weekend. See `docs/runbook.md` ("Off-peak scheduling") for
the operational recipe, the `schedule_paused` / `schedule_resumed` events,
`serve --ignore-schedule`, and how `peek` surfaces the pause state.

## 中文摘要

主要小节：`[agent]` 命令模板、`[runtime]` 工作目录与日志目录、`[prompt]` 提示词位置、
`[phases]` 可选阶段轮转、`[vcs]` stash 控制、`[monitor]` 可选自动停服策略。
