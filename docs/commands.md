# Commands reference

All commands accept `--config PATH` (default `./agent-runner.toml`) and `--json`
where supported. Drill-down flags (`--round` / `--log` / `--events` / `--select`)
are shared between `peek`, `watch`, and `monitor`.

## At a glance

<!-- gen:verb-table -->
| Verb | Description |
|---|---|
| `init` | Scaffold agent-runner project files |
| `install` | Generate systemd user unit, enable + start |
| `uninstall` | Stop, disable, and remove systemd user unit(s) |
| `start` | Start the service |
| `stop` | Graceful stop (waits for current round) |
| `kill` | Force terminate (5s grace then SIGKILL) |
| `restart` | stop + start (use --force for kill semantics) |
| `status` | Show current service state |
| `peek` | peek project state with optional drill-down |
| `watch` | watch project state with optional drill-down |
| `events` | Query / stream events from events.jsonl by kind |
| `monitor` | Anomaly detection, narrate/events stream, or HTTP progress page |
| `serve` | Long-running supervisor loop |
| `round` | Run one round and exit |
| `upgrade` | Package upgrade with service-mode gate: orchestrated stop/start for systemd --user; package-only otherwise |
<!-- /gen:verb-table -->

## Lifecycle

### `agent-runner init`

Scaffold a new project: writes `agent-runner.toml`, `prompts/main.md`, and
appends `logs/` to `.gitignore`. By default also creates a git commit.

Flags:

- `--preset {claude,aider,gemini,codewhale}` — agent CLI preset to scaffold (default: `claude`)
- `--force` — overwrite an existing `agent-runner.toml`
- `--no-commit` — skip the initial git commit

```bash
agent-runner init                      # default: claude preset, commit
agent-runner init --preset aider       # aider preset
agent-runner init --no-commit          # skip the commit
agent-runner init --force              # overwrite an existing toml
```

### `agent-runner install [--monitor] [--system]`

Generate and install systemd user unit(s):

- Always: `~/.config/systemd/user/agent-runner@<project>.service`
- With `--monitor`: also `agent-runner-monitor@<project>.service`
- `--system`: write `/etc/systemd/system/agent-runner@<project>.service` instead;
  requires root (`sudo -E agent-runner install --system` — `-E` preserves
  `SUDO_USER`, which becomes the unit's `User=`). Enables but does **not** start:
  finish with `systemctl start agent-runner@<project>`.

After writing, runs `systemctl --user daemon-reload`, `enable`, `start`.

### `agent-runner uninstall`

Stops and disables both units, then deletes the unit files and reloads systemd.

### `agent-runner start | stop | kill | restart | status`

| Verb | Semantics | Notes |
|---|---|---|
| `start` | systemctl start (or spawn `serve` if no unit) | idempotent |
| `stop` | **graceful** (default): SIGTERM → wait for current round → exit | up to ROUND_TIMEOUT |
| `kill` | **force**: SIGTERM → 5s grace → SIGKILL | use only when stuck |
| `restart [--force]` | stop + start (`--force` uses kill semantics) | |
| `status [--json]` | service mode, active state, pid, uptime | |

### `agent-runner round`

Run one supervisor round and exit. Used internally by `serve` and systemd; you
can also invoke directly to debug.

### `agent-runner serve [--once]`

Long-running supervisor loop. Traps SIGTERM (graceful stop) and SIGINT
(graceful). Writes `serve.pid`. `--once` runs a single round then exits (debug).

### `agent-runner upgrade [--target VERSION] [--no-restart] [--config PATH]`

Upgrade the agent-runner package. Behavior depends on the detected service mode:

- **systemd --user service** (installed via `agent-runner install`): full
  orchestrated flow — stop → pip install → smoke (`--version` + `peek`) →
  start → emit `service_upgraded`. Auto-rollback on smoke failure.
- **Anything else** (system unit, foreground, no config): package-only —
  PEP 668-aware pip + `--version` smoke + pip-level rollback, emits
  `package_upgraded`, prints the restart command. Never touches your running
  service, never runs `sudo`.

`--config` is optional: when omitted (or the file is absent), `upgrade` falls
back to package-only mode automatically.

`--no-restart` forces package-only even on a systemd --user host (upgrade the
package now, restart your service yourself).

Operator walkthrough (per-deployment decision table, rollback, failure modes,
postmortem trail): see `docs/runbook.md` § "Upgrading agent-runner".

## Observation

### `agent-runner peek [flags]`

Snapshot of project state. Without flags, prints a pretty summary; with
`--json`, emits a structured ProjectState document.

```bash
agent-runner peek
agent-runner peek --json
agent-runner peek --select system.disk_used_pct
agent-runner peek --select defenses
agent-runner peek --round 42 --log         # drill into round 42, include log tail
agent-runner peek --events 50              # last 50 events
```

### `agent-runner events --kind K[,K2,...] [--window N] [--tail]`

Query or stream events.jsonl by kind. Output is always JSON Lines (one event
JSON per line). Current-month events.jsonl scope only.

```bash
# One-shot: last 5 usage records
agent-runner events --kind agent_usage_recorded --window 5

# Multi-kind OR filter
agent-runner events --kind round_end,hook_failed --window 20

# Streaming: emit each new matching event as it fires; blocks until SIGINT
agent-runner events --kind transient_error_backoff_capped --tail
```

`--window N` and `--tail` are mutually exclusive. Exit codes: 0 normal,
2 invalid arguments, 1 unreadable events file.

### `agent-runner watch [--interval N] [peek-flags]`

`peek` in a clear-and-refresh loop. Default 2s interval. Stop with Ctrl-C.

### `agent-runner monitor [--host SSH-ALIAS] [--interval N] [--mode MODE] [--port PORT] [--json]`

Anomaly-detection daemon. Runs the 11 detectors against the live state on every
poll. Without `--host`, watches local logs at default 30s interval. With
`--host`, watches a remote agent-runner over plain ssh at default 60s interval.

When OAuth-fail or disk-critical detectors fire, monitor automatically issues a
graceful stop (locally via `api.stop`; remotely via `ssh <host> 'agent-runner stop'`).
Override with `[monitor]` config block (see configuration.md).

Flags:

- `--mode {anomaly,narrate,events,http}` — output mode (default: `anomaly`). `narrate`
  streams a human-readable narrative; `events` streams raw event JSON; `http` serves
  a local progress page.
- `--port PORT` — HTTP port for `--mode http` (default: `8765`, local-only).
- `--host SSH-ALIAS` — watch a remote agent-runner via ssh (anomaly mode only).

```bash
agent-runner monitor                       # local anomaly mode
agent-runner monitor --host pi             # remote
agent-runner monitor --mode narrate        # streaming narrative
agent-runner monitor --mode http --port 9000  # HTTP progress page on port 9000
agent-runner monitor --json | jq -c        # pipe alerts to a downstream consumer
```

## 中文摘要

15 个动词，完整列表见上方动词表（自动生成）。

观察类（peek/watch/monitor）三视角对称，全部共用 `--round / --log / --events / --select / --json` 下钻参数。

服务停止两动词：`stop` 优雅、`kill` 强制。

`monitor` 检测到 OAuth 失败或磁盘超 95% 时**自动优雅停服**，避免烧 quota / 写满磁盘。
