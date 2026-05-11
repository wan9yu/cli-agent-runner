# agent-runner

Restart-on-exit supervisor for autonomous CLI agents. Each round runs the agent
once and exits; an external wrapper (launchd / systemd / bash loop) restarts.

## Status

Early development.

## Install

```bash
git clone https://github.com/wan9yu/agent-runner.git
cd agent-runner
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick start

Copy `deploy/example-agent-runner.toml` to your project, edit `agent.command`
and `prompt.file`, then:

```bash
agent-runner round       # run one round
agent-runner --status    # show last round status
agent-runner --metrics   # show last 20 metrics samples
```

For continuous operation, install one of the wrappers in `deploy/`:

- `deploy/run-loop.sh` — POSIX bash wrapper with exponential backoff
- `deploy/launchd.plist.tmpl` — Mac launchd job
- `deploy/systemd.service.tmpl` — Linux systemd unit

## Architecture

11 small modules, single-responsibility:

| Module | Responsibility |
|---|---|
| `cli.py` | argparse entry: `round` / `--status` / `--metrics` |
| `runner.py` | Orchestrate one round; pure rotation, no event branches |
| `agent_runtime.py` | Spawn agent subprocess; ROUND_TIMEOUT + process group reap |
| `prompt_loader.py` | Load `.md` prompt; strip frontmatter; inject context |
| `vcs_state.py` | Git: dirty detection, SHA-locked stash, set-based diff |
| `context_store.py` | JSON state files, atomic write |
| `config.py` | TOML loader with dataclass validation |
| `events.py` | Structured JSONL events; UTC monthly file naming |
| `startup_check.py` | Boot precondition battery (6 checks) |
| `metrics.py` | Cross-platform mem + disk + load via psutil |

## Development

```bash
# Install dev deps
pip install -e ".[dev,e2e]"

# Run unit + integration + invariants
pytest tests/ --ignore=tests/e2e

# Run e2e on remote pi (opt-in)
AGENT_RUNNER_E2E_PI=1 pytest tests/e2e/ -v

# Lint + format check
ruff check agent_runner tests
ruff format --check agent_runner tests

# Dead-code scan
vulture agent_runner/ .vulture-whitelist.py --min-confidence 80
```
