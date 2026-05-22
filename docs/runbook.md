# Operator runbook

## Install prerequisites by distro

`agent-runner install` writes a systemd unit and enables it. User-mode
installs (`agent-runner install`) require a user systemd session;
system-mode (`agent-runner install --system`, requires sudo) writes to
`/etc/systemd/system/` and works without one.

| Distro                       | User systemd default       | linger required | `--system` recommended |
|------------------------------|----------------------------|-----------------|------------------------|
| Ubuntu 22.04+ desktop        | runs                       | optional        | no                     |
| Ubuntu Server                | needs `loginctl enable-linger $USER` | required | optional         |
| Debian 12+                   | needs linger               | required        | optional               |
| dietpi (Debian-based)        | default off, dbus quirk    | often blocked   | **recommended**        |
| Raspberry Pi OS Lite         | similar to dietpi          | required        | recommended            |
| Alpine (OpenRC, no systemd)  | N/A                        | N/A             | not supported          |

### User-mode prerequisites

```bash
sudo loginctl enable-linger $USER   # persist user session at boot
# re-login or reboot, then:
agent-runner install --monitor
```

### System-mode (recommended for headless distros)

```bash
sudo -E agent-runner install --system [--monitor]
# Then manually start (system-mode does not auto-start):
sudo systemctl start agent-runner@<project>
```

`-E` preserves `SUDO_USER` so the unit's `User=` directive is set
correctly (process still runs as your user, not root).

## Daily operations

> **Config changes require restart**: editing `agent-runner.toml` does not
> hot-reload. After any TOML change, run `agent-runner restart` to pick up
> the new config. The supervisor reuses the loaded Config across all rounds
> within a single `serve` session.

### Health check

```bash
agent-runner status                                       # service running?
agent-runner peek                                         # full state snapshot
agent-runner peek --json | jq .defenses                   # what's defended
agent-runner peek --json | jq .system.agent_process_count # orphan agent count (0.1.34+)
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

## Bounded runs (stress tests, batch jobs)

`agent-runner serve` defaults to infinite-supervisor mode. For bounded
runs (stress tests, scheduled batch jobs, migration validation, dev
iteration), use the three between-rounds stop triggers:

| Trigger | Use case |
|---|---|
| `.agent-done` sentinel | Agent self-determines "I'm done" (research / refactor / bug-fix sweeps) |
| `[runtime] stop_file` | Operator graceful pause for maintenance |
| `[runtime] max_rounds` + `--max-rounds N` | Config or CLI-driven N-round bound |

All three exit cleanly with code 0 and emit a distinct event.

### Bounded job pattern (max_rounds)

For "run N rounds and stop":

```toml
[runtime]
max_rounds = 3
```

```bash
agent-runner serve --max-rounds 3 --config ./test.toml
```

Pair with systemd `Restart=on-failure` so clean exits don't respawn:

```ini
[Service]
ExecStart=... serve --config /etc/test.toml --max-rounds 3
Restart=on-failure
RestartSec=5
```

### Operator graceful pause (stop_file)

For pausing without killing in-flight rounds:

```toml
[runtime]
stop_file = "logs/stop-requested"
```

Ops workflow:

```bash
touch ~/.agent-runner/<project>/logs/stop-requested
# Supervisor finishes current round, emits stop_file_detected, exits 0
sudo systemctl status agent-runner@<project>   # verify clean exit

# To resume:
rm ~/.agent-runner/<project>/logs/stop-requested
sudo systemctl start agent-runner@<project>
```

Deletion does NOT auto-resume. Explicit `systemctl start` required.

### systemd unit pattern recommendations

```ini
# Prod (infinite supervisor) — current default
[Service]
ExecStart=... serve --config /etc/agent-runner.toml
Restart=always
RestartSec=5

# Bounded job
[Service]
ExecStart=... serve --config /etc/test.toml --max-rounds 10
Restart=on-failure
RestartSec=5
```

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

## Remote monitor & SSH trust

`agent-runner monitor --host <alias>` is built on plain SSH, not a privileged
API. Power profile:

- Reads `~/.ssh/config` for the alias (host, user, identity file,
  `StrictHostKeyChecking` policy).
- Runs `agent-runner peek --json` on the remote to collect status.
- When alerting with `auto_stop` enabled, runs `agent-runner stop` on the
  remote — a real state change.
- Default SSH behavior in many environments is `StrictHostKeyChecking
  accept-new`, which silently trusts new host keys on first connect.

### Recommended hygiene

- **Dedicated SSH key**: use a key pair not shared with your shell user's
  default identity. Add it via `IdentityFile` in `~/.ssh/config` for the
  alias.
- **Pin host key**: set `StrictHostKeyChecking yes` in the `~/.ssh/config`
  entry for the alias. Never use `no`.
- **Restrict remote user**: confine the remote account's shell access to
  `agent-runner` commands via a `command="..."` restriction in
  `~/.ssh/authorized_keys` on the server.
- **Audit `auto_stop` triggers**: a monitor stopping a remote service is a
  real state change. Verify the detector logic and thresholds before enabling
  `auto_stop` on a production remote.

### Liveness monitoring: run monitor from a separate machine

`agent-runner monitor` detects anomalies including `supervisor_stale` — the
supervisor stopped emitting events because it is stuck between rounds or dead.
But a monitor running on the *same host* as the supervisor dies when that host
dies, so it cannot report its own host's death.

For true liveness coverage, run the monitor from a **separate machine**:

    # On your laptop / a second host, NOT on the supervised host:
    agent-runner monitor --host pi

This catches both failure modes:

- Supervisor stuck on a live host → `supervisor_stale` alert (events frozen).
- Host itself dead / network gone → SSH poll fails → `monitor_remote_giveup`.

The `supervisor_stale` threshold defaults to `round_timeout_s * 1.5`. Override
with `[monitor] supervisor_stale_threshold_s = N` for projects whose legitimate
cadence — very short rounds with occasional long legitimate gaps, or phase
overrides that raise `round_timeout_s` — does not fit the derived default. Set
to `0` to disable the detector entirely.

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

## Agent self-termination

For projects with natural completion criteria (research, bug-fix sweeps,
refactors), the agent can signal "research wrapped up" by writing a sentinel
file:

```bash
# Inside the agent's logic, when it decides it's done
echo "research wrapped: hypothesis X covered" > "$AGENT_RUNNER_LOG_DIR/.agent-done"
```

`agent-runner serve` injects `AGENT_RUNNER_LOG_DIR` into the round subprocess
env. Between rounds, the supervisor checks for `.agent-done`. If present:
emits `agent_self_terminated` event (payload `{reason}`, capped 200 chars) and
exits with code 0.

The sentinel is cleaned at serve startup so a stale flag from a previous run
doesn't immediately stop a fresh `serve` invocation.

To inspect terminations: `grep agent_self_terminated {log_dir}/events-*.jsonl`.

## Per-round stdout/stderr log files

Each round subprocess writes its merged stdout+stderr to
`{log_dir}/round-<N>.log`, where `<N>` matches the `round_num` field in
`events.jsonl`. A symlink `{log_dir}/round-current.log` always points to the
active round's log — `tail -F {log_dir}/round-current.log` for live view.

Retention configurable via `runtime.round_log_retention` (default 100). At
each serve startup, files beyond the retention count (by mtime) are pruned.

Note for systemd deployments: journalctl will no longer show per-round agent
output — supervisor lifecycle messages remain in journal, raw agent output
lives in the round log files.

## HTTP progress endpoint

For browser-friendly live visibility:

```
agent-runner monitor --mode http --port 8765 --config /path/to/agent-runner.toml
```

Open `http://localhost:8765/` to see a 5-section page (auto-refresh 5s):
1. Round-level state (round_num, phase, last outcome, duration)
2. High-level narrative (last 50 lines of `runtime.narrative_file`, default `log_dir/narrative.md`)
3. Recent events (last 20)
4. Round stdout/stderr tail (last 50 lines)
5. Self-termination flag status

JSON endpoint at `/api/state` for scripts.

Local-only (binds 127.0.0.1, no auth). For remote monitoring use
`--mode anomaly`. Zero new dependencies — stdlib `http.server`.

If the port is in use, monitor exits with code 1 and a structured stderr
message. Pick another port via `--port`.

## Long-running research project (24×7 unattended)

For research-style work where the agent autonomously explores a question
across many rounds and self-terminates when "done", the pattern below
combines diverge/converge phase rotation, multi-file prompt concat, a
thin operator-facing synthesis file, and the `.agent-done` sentinel.

### Project layout

```
my-research/
├── agent-runner.toml
├── prompts/
│   ├── _common.md       # preamble: goal, success criteria, guardrails
│   ├── diverge.md       # phase=diverge round instructions
│   └── converge.md      # phase=converge round instructions
├── narrative.md         # agent-maintained thin synthesis (operator-facing)
├── rounds/
│   └── R<N>.md          # per-round detail file (created by agent each round)
└── outputs/
    └── recommendation.md  # final deliverable on convergence
```

### TOML pattern

Use `agent-runner init --preset claude` to scaffold a current preset
(includes `--dangerously-skip-permissions`, `--verbose`, `--output-format
stream-json` — the latter required for `claude_error_detector` to parse
JSONL and emit `agent_usage_recorded` / `transient_error_detected`).

```toml
[agent]
command = [
  "claude", "--model", "claude-opus-4-7",
  "--dangerously-skip-permissions",
  "--verbose", "--output-format", "stream-json",
]
prompt_arg_template = ["-p", "{prompt}"]

[runtime]
work_dir = "/home/user/my-research"
log_dir = "logs"                  # relative — resolved against work_dir (0.1.17+)
narrative_file = "narrative.md"
restart_delay_s = 30

[prompt]
files = ["prompts/_common.md", "prompts/diverge.md"]  # default before phase rotation
concat_separator = "\n\n---\n\n"

[phases]
list = ["diverge", "converge"]

[phases.diverge]
prompt.files = ["prompts/_common.md", "prompts/diverge.md"]

[phases.converge]
prompt.files = ["prompts/_common.md", "prompts/converge.md"]

[vcs]
dirty_action = "ignore"   # agent does its own commits during round body
```

### Self-termination

Agent writes `$AGENT_RUNNER_LOG_DIR/.agent-done` when it considers the
research converged (per criteria in `prompts/_common.md`):

```bash
echo "converged: <one-line summary>" > "$AGENT_RUNNER_LOG_DIR/.agent-done"
```

Supervisor detects between rounds and exits cleanly with code 0.

### Memory awareness on Pi-class hardware

For Raspberry Pi (≤512 MB RAM), include explicit memory-awareness in
`prompts/_common.md`:
- Use `head` / `tail` / `grep -m`, never `cat` on large files
- Avoid recursive directory listings
- Check `free -h` before expensive operations

### Operator monitoring

```bash
agent-runner monitor --mode http --port 8765 --config <toml>
# SSH-tunnel from your laptop:
ssh -L 8765:127.0.0.1:8765 <pi-host>
# Open http://localhost:8765/ in browser
```

### Going truly 24×7 (systemd)

```bash
agent-runner install --user --config <toml>
systemctl --user start agent-runner@<project>
systemctl --user enable agent-runner@<project>  # restart on Pi reboot
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

### Transient errors (rate limits + 5xx + timeouts)

**Symptom:** `[WARN] rate_limit_active` alert from monitor;
`transient_error_detected` events appear in events.jsonl; supervisor
pauses round dispatch.

The built-in `claude_error_detector` classifies transient errors into
4 buckets:

- `rate_limit_account` — claude.ai OAuth 5-hour quota exhausted
  (`rate_limit_event.rateLimitType = "five_hour"`). `reset_at_epoch`
  is server-provided.
- `rate_limit_model` — claude.ai infrastructure 429 (no 5h-type hint).
  60s default back-off.
- `api_transient_5xx` — server outage (500/502/503/504). 60s default.
- `api_timeout` — 408 timeout. 30s default.

For `rate_limit_account` only, a legacy `rate_limit_rejected` event is
also dual-emitted for pre-0.1.23 consumers.

**Default behavior (`transient_error_action = "back_off"`):**

The supervisor sleeps until `reset_at_epoch` (plus a 5–30s jitter),
then emits `transient_error_recovered` and resumes automatically. No
operator action needed during back-off.

**Forcing immediate stop instead:**

```toml
# agent-runner.toml
[runtime]
transient_error_action = "stop"   # 0.1.23+ canonical name
# rate_limit_action = "stop"      # deprecated alias, still accepted
```

This causes the supervisor to emit `agent_self_terminated` with
`reason = "transient_error"` and exit cleanly. Restart with
`agent-runner start` after the underlying issue resolves.

**Checking throttle status:**

```bash
agent-runner peek --json | python3 -m json.tool | grep -A5 rate_limit
# "rate_limit": null  → not throttled
# "rate_limit": { "throttled_until_epoch": ... }  → throttled
```

**Monitor alert:**

The `rate_limit_active` detector fires a `warning`-severity alert while
throttled (for any classification). It clears automatically when
`transient_error_recovered` is emitted. No configuration needed;
auto-stop is NOT triggered.

See `docs/migrations/0.1.23.md` (initial 4-bucket classifier) and
`docs/migrations/0.1.27.md` (supervisor consumer guide with dispatch
table + back-off recipe per bucket).

## 中文摘要

故障手册按场景：OAuth/auth 401（自动停服 → 刷新对应 provider 凭据，例如
claude 用 `claude /login`、aider 用 `export OPENAI_API_KEY=...` 后 `start`）；
网络抖（仅报警，自愈）；orphan stash 抢救（**用 SHA 不要用 stash@{N}**）；
服务启不来（看 journalctl 找 STARTUP FAIL）；卡轮 → `kill`；磁盘 95%
自动停服 → 清理日志后 `start`。
