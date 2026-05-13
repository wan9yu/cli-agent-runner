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
