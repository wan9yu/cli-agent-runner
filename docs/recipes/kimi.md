# Running agent-runner with Kimi

[Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) is Moonshot AI's
terminal coding agent, built on the K2.7/K3 code models. It runs one prompt and
exits via `kimi -p`, fitting agent-runner's per-round lifecycle. There are two
ways to run agent-runner against Kimi — pick by which agent experience you want.

> **Which "kimi"?** This preset targets **Kimi Code CLI** — GitHub
> `MoonshotAI/kimi-code`, command `kimi`, installed via `brew install kimi-code`
> or `npm i -g @moonshot-ai/kimi-code`. Do **not** confuse it with the older
> `MoonshotAI/kimi-cli` (a separate Python tool run as `uv run kimi`) —
> `kimi-code` can even import a legacy install with `kimi migrate`. Verify a
> package before installing: `brew info kimi-code` should read `From:
> homebrew-core`, and `npm view @moonshot-ai/kimi-code repository.url` should
> point at `github.com/MoonshotAI/kimi-code`.

## Option A — the native Kimi Code CLI (`--preset kimi`)

The first-class Kimi experience: Moonshot's own agent, its subagents, MCP, and
skills.

### Prerequisites

- `kimi` installed and on PATH:
  ```bash
  brew install kimi-code          # or: npm i -g @moonshot-ai/kimi-code
  ```
  (or the install script at <https://code.kimi.com>)
- Model access on the supervisor host. The cleanest unattended option is a
  self-contained model spec in the environment — no interactive login, no
  `config.toml`. `kimi -p` reads these four variables:
  ```bash
  export KIMI_MODEL_NAME=kimi-k2.7-code          # or another Moonshot model id, e.g. kimi-k3[1m]
  export KIMI_MODEL_API_KEY="$YOUR_MOONSHOT_KEY"
  export KIMI_MODEL_BASE_URL=https://api.moonshot.ai/v1   # China key: https://api.moonshot.cn/v1
  export KIMI_MODEL_PROVIDER_TYPE=openai         # must be one of kimi / anthropic / openai; Moonshot = openai
  ```
  `KIMI_MODEL_API_KEY` is required whenever `KIMI_MODEL_NAME` is set. Get a key
  from the Moonshot platform (<https://platform.kimi.ai/console/api-keys>, or
  `platform.moonshot.cn` for a China key) — note the endpoint must match the
  key's region (`.ai` vs `.cn`), or you get `401 Invalid Authentication`.

  Alternatively, a one-time `kimi login` on the host (device-code OAuth;
  credentials cache at `~/.kimi-code/credentials/`) configures provider + model
  in one step. The supervisor cannot complete an interactive login itself, so do
  it up front. A raw `KIMI_API_KEY` alone is not enough — `kimi -p` also needs
  the model configured. `kimi doctor` validates config.
- A git repo as `work_dir`.

### Scaffold

```bash
agent-runner init --preset kimi
```

This writes an `agent-runner.toml` whose agent command is
`kimi --output-format stream-json` with the prompt passed on argv (`-p`).
`kimi -p` runs headlessly and auto-approves tool calls on its own — it rejects
`--yolo` / `--auto` (`Cannot combine --prompt with --yolo`), so the preset omits
them. The model is whatever `~/.kimi-code/config.toml` sets as `default_model`;
pin a specific one by adding `"-m", "kimi-k3[1m]"` to the `command` array.

### Note on the prompt on argv

`kimi -p` takes the prompt as a command-line argument (it has no stdin path, so
unlike the `claude` preset it cannot use `prompt_delivery = "stdin"`). If your
prompt can contain a token the agent later matches with `pkill -f`, prefer
Option B, where the prompt travels on the Claude Code binary's stdin instead.

## Option B — Kimi K3 through the `claude` preset (no native CLI needed)

Kimi K3 is served over a Moonshot **Anthropic-compatible** endpoint, so Claude
Code can drive it directly. This reuses agent-runner's `claude` preset — you get
the Claude Code agent with Kimi K3 as the model. No Kimi CLI install required.

Scaffold with the claude preset, then edit the generated `agent-runner.toml`:
point the model at Kimi and add the Moonshot endpoint to `[agent.env]`.

```bash
agent-runner init --preset claude
```

```toml
[agent]
# swap the default --model value for the Kimi K3 id:
command = ["claude", "--model", "kimi-k3[1m]",
           "--dangerously-skip-permissions",
           "--verbose", "--output-format", "stream-json"]
prompt_arg_template = ["-p"]
prompt_delivery = "stdin"
name = "claude"

[agent.env]
# Moonshot's Anthropic-compatible endpoint (per Moonshot's official Claude Code
# guide, https://platform.kimi.ai/docs/guide/claude-code-kimi). Set every model
# tier — omitting one silently falls through in that scenario.
ANTHROPIC_BASE_URL = "https://api.moonshot.ai/anthropic"
ANTHROPIC_AUTH_TOKEN = "sk-...your-moonshot-key..."   # NOT ANTHROPIC_API_KEY
ANTHROPIC_MODEL = "kimi-k3[1m]"
ANTHROPIC_DEFAULT_OPUS_MODEL = "kimi-k3[1m]"
ANTHROPIC_DEFAULT_SONNET_MODEL = "kimi-k3[1m]"
ANTHROPIC_DEFAULT_HAIKU_MODEL = "kimi-k3[1m]"
CLAUDE_CODE_SUBAGENT_MODEL = "kimi-k3[1m]"
```

The token is a Moonshot AI Open Platform key from
<https://platform.kimi.ai/console/api-keys>. Values above are quoted from
Moonshot's official guide; check that page for the current model id and any
added tier variables before relying on them.

## Which to use

Use **Option A** for the genuine Kimi Code CLI agent. Use **Option B** if you
already run the `claude` preset and only want to swap the model, or if you want
the prompt kept off argv. Both leave every other agent-runner default (defenses,
monitor, dirty-tree handling) unchanged.
