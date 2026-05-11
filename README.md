> **[中文](README.zh.md)** · English

[![CI](https://github.com/wan9yu/cli-agent-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/wan9yu/cli-agent-runner/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/cli-agent-runner.svg)](https://pypi.org/project/cli-agent-runner/) [![Downloads](https://img.shields.io/pypi/dm/cli-agent-runner.svg)](https://pypi.org/project/cli-agent-runner/) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![codecov](https://codecov.io/gh/wan9yu/cli-agent-runner/branch/main/graph/badge.svg)](https://codecov.io/gh/wan9yu/cli-agent-runner)

# agent-runner

A restart-on-exit supervisor for autonomous CLI agents. Spawn an agent (Claude
Code, a custom CLI, anything) round-after-round under defenses that prevent
the failure modes that bite in production: stuck rounds, orphan commits,
OAuth burn loops, full disks, runaway memory.

```
┌──────────────────────────────────────────┐
│ Layer 3: The Witness (monitor)           │  9 detectors + auto-stop
├──────────────────────────────────────────┤
│ Layer 2: The Loop (serve, ~60 LOC)       │  signal-trapping restart loop
├──────────────────────────────────────────┤
│ Layer 1: The Round (round)               │  one agent invocation
└──────────────────────────────────────────┘
```

## Install

```bash
pip install cli-agent-runner
```

The installed CLI command is `agent-runner` (the PyPI distribution name is
prefixed for namespace disambiguation; the import name and command are not).

## Quick start

```bash
cd your-project
agent-runner init                 # scaffold agent-runner.toml + prompts/main.md
$EDITOR agent-runner.toml         # point agent.command at your CLI
agent-runner install --monitor    # systemd user units for serve + monitor
agent-runner status               # confirm running
agent-runner peek                 # snapshot of project state
agent-runner monitor              # live anomaly detection
```

Full walkthrough: [`docs/quickstart.md`](docs/quickstart.md).

## 13 verbs

| Lifecycle | Observation |
|---|---|
| `init` / `install` / `uninstall` | `peek` — state snapshot |
| `start` / `stop` / `kill` / `cancel` | `watch` — peek in a refresh loop |
| `restart` / `status` | `monitor` — 9 detectors, alerts, auto-stop |
| `round` / `serve` | |

Verb reference: [`docs/commands.md`](docs/commands.md).

## Defenses (built in)

11 named defenses, structured as data — see `agent-runner peek --select defenses`.
Each carries the historical incident it codifies and the invariant test that
guards it. Highlights:

- **round_timeout_s** — hard wall, never the agent's word on when to stop
- **process_group_isolation** — kill the round, not just the parent
- **orphan_stash_idempotency_s** — no 3-stashes-per-second pile-ups
- **sha_locked_stash** — `stash@{N}` indices drift; SHAs don't
- **set_diff_classification** — line-set comparison, not unified-diff +/- scan
- **startup_smoke_check** — refuse to run with a clearly-truncated prompt

Full list and rationale: [`docs/architecture.md`](docs/architecture.md).

## Monitor: 9 detectors

Notify only: `timeout_rate`, `hung`, `orphan_chain`, `disk_warning`,
`mem_pressure`, `smoke_fail_rate`, `network_fail`.

**Auto-stop the service** (continuing is harmful):
- `oauth_fail` — burning API quota on auth-rejected rounds
- `disk_critical` — writing to a near-full disk risks corruption

Runs locally or against a remote host via ssh:

```bash
agent-runner monitor                  # local, 30s poll
agent-runner monitor --host pi        # remote, 60s poll
agent-runner monitor --json | jq -c   # pipe to downstream consumers
```

## Documentation

- [`docs/quickstart.md`](docs/quickstart.md) — 5-step install + first round
- [`docs/commands.md`](docs/commands.md) — verb reference
- [`docs/configuration.md`](docs/configuration.md) — `agent-runner.toml` schema
- [`docs/runbook.md`](docs/runbook.md) — operator troubleshooting (OAuth, disk, orphan)
- [`docs/architecture.md`](docs/architecture.md) — 3-layer model, defenses-as-data

## Status

Phase 2 (operator surface) shipped. Phase 3 (LLM-augmented Critic) reserved —
`[llm]` config block and `agent_runner.critic` Protocol stubs are in place,
implementation TBD.

## Development

```bash
git clone https://github.com/wan9yu/cli-agent-runner.git
cd cli-agent-runner
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

./build.sh check                          # full local-CI sweep
./build.sh test                           # unit + integration only
AGENT_RUNNER_E2E_PI=1 ./build.sh e2e      # opt-in pi e2e (needs ssh alias `pi`)
```

Some `docs/*.md` blocks are generated from code — `./build.sh docs` rewrites
the `<!-- gen:* -->` regions, and `./build.sh check` verifies they are fresh.

POSIX-only (Linux, macOS). Tested under Python 3.11+ on x86_64 and aarch64.

## License

[Apache License 2.0](LICENSE).
