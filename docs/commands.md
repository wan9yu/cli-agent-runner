# Commands reference

All commands accept `--config PATH` (default `./agent-runner.toml`) and `--json`
where supported. Drill-down flags (`--round` / `--log` / `--events` / `--select`)
are shared between `peek` and `watch`.

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

<!-- gen:flags-init -->
- `--preset {aider,claude,codewhale,gemini,kimi,pi}` — Which agent CLI preset to scaffold (default: claude)
- `--force` — Overwrite existing toml
- `--commit` — git commit the new files (default)
- `--no-commit` — Skip git commit
<!-- /gen:flags-init -->

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

Flags:

<!-- gen:flags-serve -->
- `--once` — Run a single round then exit (debug)
- `--max-rounds N` — Stop after N round completions (overrides [runtime] max_rounds in config)
- `--ignore-schedule` — Run rounds regardless of [schedule] pause/run windows (testing / catch-up)
<!-- /gen:flags-serve -->

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

Smoke scope: the orchestrated path runs `--version` **and** `peek --json
--config <path>` in a fresh subprocess, so the new code parses your TOML; the
package-only path runs `--version` alone and never reads your config. Neither
runs a round or invokes hooks/detectors, and a plugin that fails to import is a
`UserWarning`, not a smoke failure — for a breaking version, self-check first
(see `docs/runbook.md` § "What the smoke covers").

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

### `agent-runner events --kind K[,K2,...] [--window N] [--tail] [--since ISO-TS]`

Query or stream events.jsonl by kind. Output is always JSON Lines (one event
JSON per line). Current-month events.jsonl scope only, except under `--since`,
whose replay spans month files.

```bash
# One-shot: last 5 usage records
agent-runner events --kind agent_usage_recorded --window 5

# Multi-kind OR filter
agent-runner events --kind round_end,hook_failed --window 20

# Streaming: emit each new matching event as it fires; blocks until SIGINT
agent-runner events --kind transient_error_backoff_capped --tail

# Resume a dropped stream: replay the backlog, then keep streaming
agent-runner events --kind round_end --tail --since 2026-07-27T10:00:00.000Z
```

`--since ISO-TS` emits every match with `ts >= ISO-TS` (no windowing), then
follows if `--tail` was given. It is at-least-once by contract: reconnect with
the last ts you saw and that one event may repeat, but nothing that fired while
you were disconnected is lost.

`--window N` is mutually exclusive with `--tail` and with `--since`. Exit codes:
0 normal, 2 invalid arguments, 1 unreadable events file.

### `agent-runner watch [--interval N] [peek-flags]`

`peek` in a clear-and-refresh loop. Default 2s interval. Stop with Ctrl-C.

### `agent-runner monitor [--mode MODE] [--host SSH-ALIAS] [--kind K,...] [--remote-config PATH] [--interval N] [--port PORT] [--json]`

Anomaly-detection daemon. Runs the 11 detectors against the live state on every
poll, watching the project's local logs at a default 30s interval.

When OAuth-fail or disk-critical detectors fire, monitor automatically issues a
graceful stop via `api.stop`. Override with the `[monitor]` config block (see
configuration.md).

Flags:

<!-- gen:flags-monitor -->
- `--host SSH-ALIAS` — Remote ssh alias — supported with --mode events only: agent-runner manages the ssh, resumes with --since after a drop, and kills the ssh process group on exit. Detection modes run on the host itself.
- `--interval SECONDS` — Poll interval (default 30s)
- `--kind K[,K2,...]` — Event kinds to relay (--host --mode events only). Default: every kind this client knows — built-ins plus locally installed plugin kinds. A kind that exists only on the remote must be named here.
- `--mode {anomaly,narrate,events,http}` — anomaly (default): alert-only; narrate: human-readable event stream; events: JSONL event stream; http: browser progress page
- `--port PORT` — HTTP port for --mode http (default 8765, local-only)
- `--remote-config PATH` — Config path ON THE REMOTE HOST for the relayed events command (--host --mode events only). Default: omit --config entirely, so the remote resolves ./agent-runner.toml in the ssh landing directory.
<!-- /gen:flags-monitor -->

Mode × `--host` matrix:

| `--mode` | local | `--host X` |
|---|---|---|
| `anomaly` (default) | 11 detectors + auto-stop | rejected (exit 1) |
| `narrate` | human-readable stream | rejected (exit 1) |
| `events` | JSONL stream of local events | **managed ssh relay** |
| `http` | progress page on 127.0.0.1 | rejected (exit 1) |

Detection is rejected remotely by design: the detectors read the supervised
host's logs and stop its service, so they must run there (see
[runbook.md](runbook.md) § "Remote event relay & SSH trust").

Relay behavior: stdout is the remote's JSONL, unmodified. Each ssh exit emits
`monitor_remote_blip` and reconnects with `--since <last relayed ts>` so the gap
is replayed (at-least-once — the boundary event may repeat). An outage longer
than `[monitor] remote_failure_tolerance_s` (default 90s) emits
`monitor_remote_giveup` and exits 1. Those two events go to the **client's**
`log_dir` — they describe this machine's link, not the remote project. The ssh
process group is killed (SIGTERM → grace → SIGKILL) on Ctrl-C, on give-up and
before each reconnect.

```bash
agent-runner monitor                       # local anomaly mode
agent-runner monitor --mode narrate        # streaming narrative
agent-runner monitor --mode http --port 9000  # HTTP progress page on port 9000
agent-runner monitor --json | jq -c        # pipe alerts to a downstream consumer
agent-runner monitor --host pi --mode events  # relay pi's event stream here
agent-runner monitor --host pi --mode events --kind round_end,oauth_fail | jq -c
```

## 中文摘要

15 个动词，完整列表见上方动词表（自动生成）。

观察类（peek/watch/monitor）三视角对称，全部共用 `--round / --log / --events / --select / --json` 下钻参数。

服务停止两动词：`stop` 优雅、`kill` 强制。

`monitor` 检测到 OAuth 失败或磁盘超 95% 时**自动优雅停服**，避免烧 quota / 写满磁盘。
