# Operator runbook

## Daily operations

### Health check

```bash
agent-runner status              # service running?
agent-runner peek                # full state snapshot
agent-runner peek --json | jq .defenses    # what's defended
journalctl --user -u agent-runner@<project> --since "1 hour ago"
```

### Routine restart

```bash
agent-runner restart             # graceful — waits for current round
```

### Stop for maintenance

```bash
agent-runner stop                # let current round finish
# ... do maintenance ...
agent-runner start
```

> **Stop ops feedback.** `agent-runner stop` prints two stderr lines —
> `agent-runner: stopping service...` then `agent-runner: stopped (Xs)` — so
> you know it completed. Typical duration is <5s. There is no progress bar by
> design; if systemd takes longer than `TimeoutStopSec`, consult the systemd
> journal for the underlying reason.

## Upgrading agent-runner

### Recommended: single command

```
agent-runner upgrade [--target X.Y.Z] --config /path/to/agent-runner.toml
```

`--target` defaults to the latest version on PyPI. To pin a specific
version (or roll back), pass `--target X.Y.Z`.

### What it does

1. Capture the currently-installed version via `agent_runner.__version__`
2. Graceful stop (waits for the current round to finish)
3. `pip install --upgrade cli-agent-runner[==<target>]`
4. Smoke check the new binary in a fresh subprocess: `agent-runner --version`
   + `agent-runner peek --json --config <path>`
5. If smoke passes: start service. Emit `service_upgraded` event.
6. If smoke fails: roll back to the previous version via
   `pip install --force-reinstall cli-agent-runner==<previous>`, sanity-smoke,
   start service, emit `service_upgrade_rolled_back` event. Exit code 1.
7. If rollback itself fails (rare): emit `service_upgrade_rollback_failed`
   event. Service stopped. Exit code 2. Manual intervention required.

### Manual rollback

`agent-runner upgrade --target <previous-version>` is the supported way to
roll back — the same command works in both directions.

### Index trust

`agent-runner upgrade` invokes `pip install` which honors your operator's
configured pip index (`pip config list`, `PIP_INDEX_URL`, `~/.pip/pip.conf`).
If your environment uses a corporate mirror or custom index, the upgrade will
fetch from there. To verify your index before upgrading: `pip config list`.

### Failure modes

| Symptom | Recovery |
|---|---|
| Stop is stuck | `agent-runner kill` → manual `pip install --upgrade ...` → `agent-runner start` |
| pip install fails (network / no PyPI) | Service is left stopped. Run `agent-runner start` to resume the previous version. Retry upgrade later. |
| Smoke fails, rollback succeeds | Service running on previous version. Investigate via `journalctl --user -u agent-runner@<project>` and the `service_upgrade_rolled_back` event's `failure_reason` field. File a bug report. |
| Smoke fails, rollback ALSO fails (rare) | Service stopped. `service_upgrade_rollback_failed` event written (best-effort). Manually: `pip install --force-reinstall cli-agent-runner==<known-good>` then `systemctl restart agent-runner@<project>`. |

### Postmortem trail

Grep events.jsonl for upgrade history:
```
grep -E "service_upgrad" {log_dir}/events-*.jsonl | jq .
```
Three event kinds are interesting:
- `service_upgraded` — clean upgrade
- `service_upgrade_rolled_back` — attempted upgrade reverted (safety net fired)
- `service_upgrade_rollback_failed` — critical: needs manual intervention

## Plugin cold-start (serve-startup hooks)

Plugins may register `ServeStartupHook` callbacks that fire once per
`agent-runner serve` invocation. The hook receives the loaded `Config` and
returns nothing.

Typical use case: seed a file or external state that subsequent rounds depend
on. Example: a plugin's `PreRoundHook` overwrites `/tmp/my-prompt.md` per
round, but the first round needs the file to already exist. A serve-startup
hook seeds it before any round runs.

### Failure behavior

If a serve-startup hook raises, `agent-runner serve` aborts with exit code 1
before entering the round loop. A `serve_startup_hook_failed` event is emitted
best-effort with payload `{hook, exc_type, exc_msg}`.

To inspect failures: `grep serve_startup_hook_failed {log_dir}/events-*.jsonl`.

Operators can disable a misbehaving hook via `[plugins] disable = ["hook_name"]`
just like any other plugin component.

## Live event stream (machine-readable)

For machine consumption (parity comparisons, custom dashboards, automation
scripts), use:

```
agent-runner monitor --mode events --config /path/to/agent-runner.toml
```

Stdout emits one event per line as JSON. Subscription begins at process-start;
historical events are not replayed (use `cat events-*.jsonl | jq .` for that).
The mode follows daily file rotation transparently.

Local-only (no `--host` support). For remote monitoring use `--mode anomaly`.

Example pipe:

```bash
agent-runner monitor --mode events | jq 'select(.event == "round_start" or .event == "round_end")'
```

## Troubleshooting

### OAuth / auth failures (agent rejects requests)

**Symptom:** `monitor` reports `[CRIT] oauth_fail — N/10 recent rounds short-exited`.
The service auto-stops by default.

**Diagnose:**

```bash
agent-runner peek --round latest --log | tail -30   # look for 401 / unauthorized
journalctl --user -u agent-runner@<project> --since "30 min ago" | grep -i 'auth\|401'
```

**Fix:**

```bash
# On the supervisor host (NOT in agent-runner's subprocess):

# For the claude preset:
claude /login
# OR refresh the API key
export ANTHROPIC_API_KEY=sk-...   # then restart your shell or systemctl --user

# For the aider preset (provider varies):
export OPENAI_API_KEY=sk-...      # or ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / etc.
aider --models                    # confirm aider sees the provider

agent-runner start
```

The `auth_fail_hint` shown in `peek` / `monitor` is preset-supplied and tells
you which env var / login command applies to your CLI.

### Network failures (connection errors)

**Symptom:** `monitor` reports `[WARN] network_fail — N/10 short-exited with network pattern`.
Default policy is **alert only** — the service keeps running so transient
outages self-heal.

**Diagnose:**

- Check upstream: https://status.anthropic.com/
- Check local DNS: `dig api.anthropic.com`
- Check Tailscale / VPN if applicable

**Fix:** Wait. If sustained > 30 minutes, investigate local network or upstream.

### Network-blip postmortem trail

When the monitor or an agent round hits network errors, two structured events
serve as the index into deeper diagnostic logs:

| Event | What it tells you | Where to look next |
|---|---|---|
| `monitor_remote_blip` | A single `monitor --host` poll failed with ssh rc=255 | Subsequent events in the same window; if a `monitor_remote_giveup` follows, supervision exited |
| `monitor_remote_giveup` | Cumulative ssh failure exceeded `remote_failure_tolerance_s` | `journalctl --user -u agent-runner-monitor@<project>` for the restart |
| `agent_network_blip` | An agent round's log matched a network pattern | `{log_dir}/rounds/R{round_num}-*.log` for the full agent output |

The events file is the index. The round log file is the body.

### Plugin-mutation postmortem trail

When a PreRoundHook mutates the agent's prompt, the audit trail is:

| Event | What it tells you | Where to look next |
|---|---|---|
| `prompt_overwritten` | A registered PreRoundHook changed the prompt file | `hook` field names the culprit; full prompt content is at `cfg.prompt.file` (re-read after the round to see what shipped to the agent) |

To pause this layer entirely (audit / debug): set `[runtime] disable_pre_round_hooks = true`.
To disable a specific hook by name: `[plugins] disable = ["entry_point_name"]`.
See `docs/architecture.md` § "Plugin injection: two paths" for the full mental model.

### Orphan stash recovery

**Symptom:** `peek` shows `orphan_stash` field with a stash ref. The previous
round exited cleanly but left uncommitted work; the supervisor stashed it.

```bash
git stash list                                       # see all stashes
git stash show -p <stash-sha>                        # inspect contents
git stash pop <stash-sha>                            # salvage
git stash drop <stash-sha>                           # abandon
```

> Always use the SHA, not `stash@{N}` — concurrent auto-stashes shift indices.

### Service won't start

```bash
systemctl --user status agent-runner@<project>
journalctl --user -u agent-runner@<project> --since "10 min ago"
# Common: STARTUP FAIL message — agent CLI missing, prompt file gone, work_dir not git
```

### Stuck round

```bash
agent-runner peek --round latest --log               # see what the agent is doing
agent-runner kill                                    # force terminate
# investigate the round log:
ls -la ~/.agent-runner/<project>/logs/rounds/        # most recent R*.log
```

### Disk pressure

**Symptom:** `[WARN] disk_warning` at >90%; `[CRIT] disk_critical` at >95% (auto-stops).

**Fix:**

```bash
# Inspect log directory size
du -sh ~/.agent-runner/<project>/logs/
# Old monthly events.jsonl files can be archived or deleted:
ls -lh ~/.agent-runner/<project>/logs/events-*.jsonl
gzip ~/.agent-runner/<project>/logs/events-2026-04.jsonl   # for example
agent-runner start
```

## 中文摘要

故障手册按场景：OAuth/auth 401（自动停服 → 刷新对应 provider 凭据，例如
claude 用 `claude /login`、aider 用 `export OPENAI_API_KEY=...` 后 `start`）；
网络抖（仅报警，自愈）；orphan stash 抢救（**用 SHA 不要用 stash@{N}**）；
服务启不来（看 journalctl 找 STARTUP FAIL）；卡轮 → `kill`；磁盘 95%
自动停服 → 清理日志后 `start`。
