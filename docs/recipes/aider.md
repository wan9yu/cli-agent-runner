# Running agent-runner with aider

[aider](https://github.com/paul-gauthier/aider) is a git-native AI pair programmer.
It auto-commits each round, fitting agent-runner's "did the work tree change?"
lifecycle naturally.

## Prerequisites

- `aider` installed: `pipx install aider-chat` (or `pip install aider-chat`).
- Your model provider's API key in env:
  - `OPENAI_API_KEY` (default models: gpt-4o, o-mini)
  - `ANTHROPIC_API_KEY` (claude-sonnet-4-5 etc.)
  - `DEEPSEEK_API_KEY`
  - `OPENROUTER_API_KEY`
  - `FIREWORKS_API_KEY`
  - …or any provider aider supports — run `aider --models` to verify the env
    var aider detects.
- A git repo to operate on (aider refuses to run outside a git worktree).

## Scaffold

```bash
git init my-project && cd my-project
agent-runner init --preset aider
```

This writes:
- `agent-runner.toml` — aider preset (command, flags, auth hint).
- `prompts/main.md` — neutral placeholder; replace with your task description.
- `.gitignore` — adds `logs/` if missing.

## Aider preset (`agent_runner/presets/aider.toml`)

```toml
[agent]
command = ["aider", "--yes-always", "--no-stream", "--analytics-disable"]
prompt_arg_template = ["--message", "{prompt}"]
name = "aider"
# [agent.env] omitted — aider needs no env injection.

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
auth_fail_hint = "Verify your model provider env var (OPENAI_API_KEY / ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / etc.); run `aider --models` to list detected providers."
```

### Why each flag

- `--yes-always` — non-interactive confirmation. **Mandatory** for unattended
  supervisor mode; without it, aider blocks on user input.
- `--no-stream` — aider's streaming output interleaves badly with the per-round
  log file. Non-streaming gives clean line-buffered output.
- `--analytics-disable` — aider sends usage analytics by default; the supervisor
  runs in unattended contexts where the user can't see/accept that.

### What's intentionally not configured

- **Model selection** is delegated to aider's own env detection. To pin a
  specific model, add it to `command`: e.g.
  `command = ["aider", "--model", "gpt-4o", "--yes-always", ...]`.
- **Auto-commit is left ON** (aider's default). agent-runner's
  `vcs_state.detect_dirty_files()` invariants assume the agent commits its
  own work each round; disabling aider auto-commits would leave every round's
  output as "orphan" work to stash.

## Picking a model

aider auto-detects providers from env vars. Common patterns:

```bash
export OPENAI_API_KEY=sk-...                      # GPT-4o, o-mini, etc.
export ANTHROPIC_API_KEY=sk-ant-...               # Claude models
export DEEPSEEK_API_KEY=...                       # DeepSeek Coder
```

Pin a specific model in `[agent].command`:
```toml
command = ["aider", "--model", "deepseek/deepseek-coder", "--yes-always", "--no-stream", "--analytics-disable"]
```

## Known limitations

- **Large diffs in stdout** — aider's `--no-stream` is line-buffered but still
  prints the full diff per turn. Per-round log files can grow to ~MB for
  large refactors. Log rotation is not yet configurable; clean up
  `~/.agent-runner/<project>/logs/rounds/` periodically.
- **Auto-commit messages** — aider writes its own commit messages
  ("aider: <subject>"). agent-runner does not control this; if you need a
  uniform commit style, configure `aiderrc.yml` separately.
- **No JSON-events stream** — unlike `claude --output-format stream-json`,
  aider doesn't emit structured events. Per-round outcome is detected via
  exit code + dirty-tree check (which is the same model agent-runner uses for
  any CLI).

## Troubleshooting

| Symptom | Probable cause |
|---|---|
| `aider: command not found` | aider not on PATH — `pipx install aider-chat` |
| `No LLM provider detected` | provider env var not exported — run `aider --models` |
| Round exits in <2s with non-zero | likely auth failure; check `peek` and `~/.agent-runner/<project>/logs/rounds/R*.log` for the actual provider error |
| `oauth_fail` alert in `peek` | provider auth-failure pattern detected. Hint = "Verify your model provider env var (…)". Re-export the right env var and restart. |
| Round runs but no commits | `--auto-commits` may have been disabled in your custom `command` — restore the default ON behavior |

See also: [`docs/quickstart.md`](../quickstart.md), [`docs/configuration.md`](../configuration.md).
