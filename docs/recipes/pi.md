# Running agent-runner with Pi

[Pi Coding Agent](https://github.com/earendil-works/pi) is a minimal, extensible
terminal coding agent (read/write/edit/bash tools). It runs one prompt and exits
via `pi -p`, fitting agent-runner's per-round lifecycle. The `pi` preset is
provider-agnostic — you pick the model.

> **Which "pi"?** This preset targets the **Pi Coding Agent** — GitHub
> `earendil-works/pi`, npm `@earendil-works/pi-coding-agent` (not the older
> `@mariozechner/pi-coding-agent`), command `pi`. Verify before installing:
> `npm view @earendil-works/pi-coding-agent repository.url` should point at
> `github.com/earendil-works/pi`. Pi's own docs install with `--ignore-scripts`.

## Prerequisites

- `pi` installed and on PATH:
  ```bash
  npm i -g --ignore-scripts @earendil-works/pi-coding-agent
  ```
  (or the install script at <https://pi.dev>)
- A provider + model configured (see Provider auth below). Pi has no default
  model — with no `--model` it falls back to `--provider google`, so the preset
  requires you to set `--model` explicitly.
- A git repo as `work_dir` — agent-runner starts the agent in it (pi itself
  has no `--cwd` flag).

## Scaffold

```bash
agent-runner init --preset pi
```

This writes an `agent-runner.toml` whose agent command is
`pi -p -na --mode json --model PROVIDER/MODEL`. **Replace `PROVIDER/MODEL`**
with a provider/model you have configured — our setup is Kimi K3 via Moonshot:

```toml
command = ["pi", "-p", "-na", "--mode", "json", "--model", "moonshot/kimi-k3"]
```

Notes:
- **Runs unconfined, unattended, with no confirmation.** pi executes
  bash/write/edit in "full YOLO" mode with no per-call approval — so unlike the
  `claude` / `gemini` / `kimi` presets there is no `--yolo` flag to add, and no
  interactive confirmation to fall back to. agent-runner supervises lifecycle
  but does not sandbox the agent, so an unattended pi round runs arbitrary
  shell in `work_dir` unprompted — point it only at a repo/host where that is
  acceptable. To narrow pi itself: `--tools read,grep,find,ls` is pi's
  documented read-only mode, and a pi extension's `tool_call` hook can veto
  calls (`{ block: true }`) — load a policy extension via an explicit
  `-e <path>`, because `-na` (below) means repo-local `.pi/` extensions never
  load.
- **`-na` pins project trust off.** Without it, a saved trust decision for
  `work_dir` or any parent silently loads repo-local pi resources in `-p` runs
  (`.pi/` settings, extensions, skills — and `.pi/SYSTEM.md` **replaces** the
  system prompt). Drop `-na` only for a repo whose `.pi/` you control and want.
- **`round_timeout_s` is the only brake.** pi has no turn cap, runtime timeout,
  or token budget of its own — agent-runner's wall-clock `round_timeout_s`
  (preset default 1800s) is the sole thing that ends a runaway round.
- **Log volume.** `--mode json` is a verbose JSONL event stream; switch to
  `--mode text` for cleaner round logs — agent-runner needs no specific format.
- `PI_OFFLINE = "1"` (in `[agent.env]`) suppresses pi's startup auto-update and
  catalog refresh; it does not block inference.

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

Export the key on the supervisor host, then run with `--model moonshot/kimi-k3`:

```bash
export KIMI_MODEL_API_KEY="$YOUR_MOONSHOT_KEY"
```

`baseUrl` must match your key's region — `api.moonshot.cn` for a China key,
`api.moonshot.ai` for an international one (same `.cn`/`.ai` rule as
[kimi.md](kimi.md); a mismatch returns `401`). Validate with a one-shot:
`pi -p --model moonshot/kimi-k3 "say ok"`.

### Other providers

Set the provider's env key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) or run
`pi /login` once to store credentials in `~/.pi/agent/auth.json`, then set
`--model provider/id` (e.g. `--model openai/gpt-4o`). pi can also authenticate
Anthropic with a Claude Pro/Max **subscription** via `pi /login`, but for an
unattended supervisor prefer a real API key — subscription tokens are intended
for official Anthropic clients (Anthropic's ToS), community workarounds are
fragile, and billing is unclear.
