# Running agent-runner with Pi

[Pi Coding Agent](https://github.com/earendil-works/pi) is Mario Zechner's
minimal, aggressively-extensible terminal coding agent (read/write/edit/bash
tools; 15+ providers normalized at the API layer). It runs one prompt and exits
via `pi -p`, fitting agent-runner's per-round lifecycle. The `pi` preset is
provider-agnostic — you pick the model.

> **Which "pi"?** This preset targets the **Pi Coding Agent** — GitHub
> `earendil-works/pi`, npm `@earendil-works/pi-coding-agent`, command `pi`.
> The older personal scope `@mariozechner/pi-coding-agent` (repo
> `badlogic/pi-mono`) is the predecessor of the same tool. Verify before
> installing: `npm view @earendil-works/pi-coding-agent repository.url` should
> point at `github.com/earendil-works/pi`. Pi's own docs install with
> `--ignore-scripts` (supply-chain hygiene).

## Prerequisites

- `pi` installed and on PATH:
  ```bash
  npm i -g --ignore-scripts @earendil-works/pi-coding-agent
  ```
  (or the install script at <https://pi.dev>)
- A provider + model configured (see below). Pi has **no universal default
  model** — with no `--model` it falls back to `--provider google`, so the
  preset requires you to set `--model` explicitly.
- A git repo as `work_dir`.

## Scaffold

```bash
agent-runner init --preset pi
```

This writes an `agent-runner.toml` whose agent command is
`pi -p --mode json --model PROVIDER/MODEL`. **Replace `PROVIDER/MODEL`** with a
provider/model you have configured (our setup uses `moonshot/kimi-k3`, below).

Notes:
- **No auto-approve flag needed.** Pi runs tool calls in "full YOLO" mode — it
  executes bash/write/edit with no per-call confirmation, so unlike the `claude`
  / `gemini` / `kimi` presets there is no `--yolo` / `--dangerously-skip-permissions`
  flag to add. `-a` / `--approve` only governs trust of *project-local pi
  extensions/skills* (add it to the `command` array if your repo ships them;
  `AGENTS.md` / `CLAUDE.md` are discovered regardless unless you pass `-nc`).
- **Log volume.** `--mode json` emits a structured JSONL event stream, but it is
  verbose (every thinking delta repeats the full message state). For cleaner
  human-readable round logs, change `--mode json` to `--mode text` in the
  `command` array — agent-runner needs no specific format from pi.
- `PI_OFFLINE = "1"` (in `[agent.env]`) suppresses pi's startup auto-update and
  model-catalog refresh; it does **not** block inference.

## Provider auth

### Primary path — Kimi K3 via Moonshot (what we run)

Pi speaks OpenAI-compatible endpoints, so define a custom provider in
`~/.pi/agent/models.json`:

```json
{
  "providers": {
    "moonshot": {
      "baseUrl": "https://api.moonshot.cn/v1",
      "api": "openai-completions",
      "apiKey": "$KIMI_MODEL_API_KEY",
      "models": [
        { "id": "kimi-k3", "name": "Kimi K3" }
      ]
    }
  }
}
```

Then export the key on the supervisor host and point `--model` at it:

```bash
export KIMI_MODEL_API_KEY="$YOUR_MOONSHOT_KEY"   # China key → api.moonshot.cn
# in agent-runner.toml: --model moonshot/kimi-k3
```

The endpoint must match the key's region: a China key uses
`https://api.moonshot.cn/v1` (console `platform.moonshot.cn`); an international
key uses `https://api.moonshot.ai/v1` (console `platform.kimi.ai`). A mismatch
returns `401 Invalid Authentication`. `kimi-k3` is the model id (no `[1m]`
suffix). Validate with a one-shot: `pi -p --model moonshot/kimi-k3 "say ok"`.

### Other providers

Set the provider's env key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, …) or run `pi /login` once to store credentials in
`~/.pi/agent/auth.json`, then set `--model provider/id` (e.g.
`--model anthropic/claude-sonnet-4-5`, `--model openai/gpt-4o`). `pi --list-models`
lists catalog models (custom `models.json` providers work at invocation even
when not listed).

### Claude Pro/Max subscription (optional — read the caveats)

Pi can authenticate to Anthropic with a Claude Pro/Max **subscription** instead
of an API key: `pi /login` offers a "Claude Pro/Max" option, and the community
`pi-claude-auth` extension reuses Claude Code's existing OAuth credentials
(macOS Keychain `Claude Code-credentials*` / `~/.claude/.credentials.json`),
seeding `~/.pi/agent/auth.json` with auto-refresh.

Do not rely on this for an unattended supervisor:
- **Terms of Service.** The `pi-claude-auth` extension's own page states Claude
  Pro/Max subscription tokens should only be used with official Anthropic
  clients — it is a community workaround that may stop working when Anthropic
  changes their OAuth infrastructure.
- **Fragile.** Multiple open upstream issues track subscription auth breaking
  and OAuth-refresh failures.
- **Billing unclear.** Third-party subscription use may be billed as per-token
  API usage rather than drawn from your plan.

For a long-running supervisor, prefer a real API key (the Moonshot path above),
which agent-runner's `oauth_fail` detector can also reason about cleanly.
