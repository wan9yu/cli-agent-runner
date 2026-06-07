# Running agent-runner with CodeWhale

[CodeWhale](https://github.com/Hmbown/CodeWhale) is a DeepSeek-powered terminal
agent. It runs one-shot via `codewhale exec --auto`, fitting agent-runner's
per-round lifecycle naturally.

## Prerequisites

- `codewhale` installed (ships both `codewhale` and `codewhale-tui`; both must
  be on PATH):
  ```bash
  npm i -g codewhale
  ```
  (or via cargo/brew — see the CodeWhale docs for alternative install methods)
- DeepSeek API key available to codewhale via one of:
  - `DEEPSEEK_API_KEY` environment variable on the supervisor host, **or**
  - a key saved via `codewhale auth set` (resolution order: config > keyring > env)
- A git repo as `work_dir` (required for VCS state tracking).

## Scaffold

```bash
git init my-project && cd my-project
agent-runner init --preset codewhale
```

This writes:
- `agent-runner.toml` — codewhale preset (command, flags, auth hint).
- `prompts/main.md` — neutral placeholder; replace with your task description.
- `.gitignore` — adds `logs/` if missing.

## CodeWhale preset (excerpt of `agent_runner/presets/codewhale.toml`)

```toml
[agent]
command = ["codewhale", "exec", "--auto", "--output-format", "stream-json"]
prompt_arg_template = ["{prompt}"]
name = "codewhale"
# [agent.env] omitted — DeepSeek key is ambient (env or codewhale keyring).

[runtime]
work_dir = "."
log_dir = "~/.agent-runner/{project}/logs"
round_timeout_s = 1800
restart_delay_s = 3

[prompt]
file = "./prompts/main.md"
inject_context = true

[vcs]
dirty_action = "stash"
stash_idempotency_s = 5

[monitor]
auth_fail_hint = "Run `codewhale auth status` to inspect provider/credentials, or set DEEPSEEK_API_KEY on the supervisor host."
```

### Why each flag

- `exec` — one-shot execution mode (non-interactive, no TUI).
- `--auto` — non-interactive confirmation; **mandatory** for unattended supervisor
  mode.
- `--output-format stream-json` — emits NDJSON to stdout; required so the
  `codewhale_error_detector` plugin can parse usage records. Without this flag
  the plugin receives human-readable text and emits no `agent_usage_recorded`
  events.

### What's intentionally not configured

- **No `[agent.env]`** — the DeepSeek key is resolved by codewhale from the
  ambient environment or its own keyring. Set `DEEPSEEK_API_KEY` on the
  supervisor host rather than in the TOML.

## What the detector emits

The built-in `codewhale_error_detector` plugin parses the round log tail after
each round completes:

- **`agent_usage_recorded`** — emitted from the `{"type":"metadata","meta":{...}}`
  terminal record. Carries `model`, `input_tokens`, `output_tokens`. `cost_usd`
  is always `None` (codewhale's stream-json output does not expose USD cost).
- **`transient_error_detected`** — emitted only when a `{"type":"error"}` record
  maps to an existing classification bucket (`rate_limit_model`, `api_transient_5xx`,
  `api_timeout`). The only observed error so far is auth failure, which is **not**
  a transient bucket — it surfaces via the monitor's `oauth_fail` detector instead.

## Troubleshooting

| Symptom | Probable cause |
|---|---|
| `codewhale: command not found` | codewhale not on PATH — `npm i -g codewhale` |
| Round short-exits with non-zero exit code | likely auth failure; check `peek` and `~/.agent-runner/<project>/logs/rounds/R*.log` for the error record |
| `oauth_fail` alert in `peek` | DeepSeek auth failure detected. Hint: "Run `codewhale auth status`…". Check key validity and re-export `DEEPSEEK_API_KEY`. |
| No `agent_usage_recorded` events | `--output-format stream-json` may be missing from command; verify the preset was applied correctly |
| `codewhale auth status` shows no key | Run `codewhale auth set` to save a key, or export `DEEPSEEK_API_KEY` before starting the supervisor |

See also: [`docs/quickstart.md`](../quickstart.md), [`docs/configuration.md`](../configuration.md).
