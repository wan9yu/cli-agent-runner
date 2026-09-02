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

> **Restart after any TOML change, to be safe**: each round runs as its own
> subprocess and re-reads `agent-runner.toml` fresh, so a change to per-round
> fields (agent command, prompt, per-phase overrides) already takes effect on
> the very next round. `serve` itself, though, loads the config once at
> startup and reuses that same copy for the whole session — schedule windows,
> phase rotation, the outer round-timeout ceiling, and log retention keep the
> OLD values until you run `agent-runner restart`. When in doubt, restart.

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
# Prod (infinite supervisor) — current default (0.2.11+)
[Service]
ExecStart=... serve --config /etc/agent-runner.toml
Restart=on-failure
RestartPreventExitStatus=78 75   # config_broken (78) / crash_loop (75) stay stopped
RestartSec=3

# Bounded job
[Service]
ExecStart=... serve --config /etc/test.toml --max-rounds 10
Restart=on-failure
RestartSec=5
```

## Off-peak scheduling (0.2.7+)

Restrict the supervisor to off-peak hours — for example, to avoid a provider's
peak-pricing window — without stopping the service. `[schedule]` gates the serve
loop **between rounds**: when a pause window is active the supervisor idle-sleeps
instead of launching the next round, and it auto-resumes when the window closes.
An in-flight round is **never** killed by a boundary, so no work is lost.

```toml
[schedule]
timezone = "Asia/Shanghai"
pause_windows = ["Mon-Fri 09:00-12:00", "Mon-Fri 14:00-18:00"]
```

That is the current DeepSeek off-peak policy: rounds pause through the provider's
Mon–Fri peak hours and run at every other hour, including the full weekend.
Window syntax (`[WEEKDAYS ]HH:MM-HH:MM`, end-exclusive, midnight-wrap, weekday
prefixes) and the `run AND NOT pause` evaluation rule are documented in
`docs/configuration.md` (`[schedule]`).

**What you observe:**

- Entering a pause emits `schedule_paused` (`active_window`, `resume_at`,
  `timezone`); resuming emits `schedule_resumed` (`paused_for_s`).
- `agent-runner peek` surfaces the current pause state — the `schedule` field in
  `peek --json`, and the same in the generic pretty output — so you can see
  `paused until <resume_at>` at a glance.
- The monitor does **not** raise `supervisor_stale` during an intentional pause:
  the silence is expected, and staleness suppression holds until `resume_at`
  plus one staleness window (a supervisor that actually died mid-pause still
  alarms once that bound passes).

**Forced catch-up.** To run regardless of the windows — a one-off backlog drain,
or testing — start serve with `--ignore-schedule`:

```bash
agent-runner serve --config /etc/agent-runner.toml --ignore-schedule
```

`agent-runner round` (a manual one-shot round) is never gated by `[schedule]`, so
you can always drive a single round by hand irrespective of the schedule.

## Mixed-model rotation (0.2.9+)

Run each round under a different model — to spread load across providers, keep a
cheaper model on the routine phases, or hold each provider to its own off-peak
window. `[phases.<name>.agent]` gives each phase its own agent command, and
`[phases.<name>.schedule]` its own windows; `phase_policy` decides what happens
when the rotation lands on a phase whose window is shut. Field-level semantics
(the four sub-tables, `agent` merges / `schedule` replaces, the never-overridable
list) are in `docs/configuration.md` (`[phases.<name>]`).

```toml
[agent]
command = ["codewhale", "exec", "--auto", "--output-format", "stream-json"]
prompt_arg_template = ["{prompt}"]

[runtime]
work_dir = "/srv/research"
log_dir = "logs"
round_timeout_s = 1800

[phases]
list = ["deepseek", "glm", "qwen"]
phase_policy = "skip"             # closed window → try the next phase, don't idle

[phases.deepseek.schedule]
timezone = "Asia/Shanghai"
pause_windows = ["Mon-Fri 09:00-18:00"]   # DeepSeek peak hours

[phases.glm.agent]
command = ["glm-cli", "run"]      # swaps command only; inherits prompt_arg_template

[phases.glm.runtime]
round_timeout_s = 3600            # heavier synthesis pass

[phases.qwen.agent]
command = ["qwen-cli", "chat"]
```

**Choosing the policy:**

- `phase_policy = "wait"` — the rotation is fixed. Round N always runs its
  rotation phase; if that phase's window is shut, the supervisor idle-sleeps
  until it opens. Use this when each phase is a distinct job that must run in
  order (diverge before converge), not an interchangeable worker.
- `phase_policy = "skip"` — the rotation is a preference order. A round whose
  phase is shut steps forward to the first phase that is open now and runs that
  instead. Use this when the phases are interchangeable providers and you care
  about keeping a round moving, not about which model ran it.

**What you observe under `skip`:**

- Each round that steps over one or more phases emits `schedule_phase_skipped`
  (`round_num`, `skipped`, `chosen`, `active_window`) — the skipped names and the
  phase actually chosen. A round that runs its first-choice phase emits nothing
  extra.
- If every phase's window is shut, the supervisor pauses exactly as an off-peak
  `[schedule]` does — `schedule_paused` / `schedule_resumed`, `peek` showing the
  pause — and resumes at the earliest window-open across the phases.
- Per-round attribution: `peek` and the round events carry the `phase` that ran,
  so the transcript shows which model each round used.
- **Throttle-aware skip (0.2.10; agent-keyed since 0.2.11):** a phase whose
  provider is currently throttled (rate-limited / erroring) is stepped over just
  like a closed window — `schedule_phase_skipped` records it and a healthy sibling
  runs, with no global back-off. The throttle is keyed on the **agent** (the binary
  basename of the phase's `command[0]`, matching the detector's label), so *every*
  phase sharing a throttled agent is stepped over at once — two phases both running
  `claude` won't take turns hammering one rate-limited key — while a phase on a
  different, healthy provider keeps running. `active_window` is empty when a throttle
  (not a window) drove the skip. If every candidate phase is throttled or shut, the
  loop waits until the earlier of a window opening or the earliest throttle
  `reset_at`; a per-agent `transient_error_recovered` breadcrumb marks each clear
  (an agent that recovers while a sibling is still throttled still gets its own).
  Only `skip` does this — `wait` and `--ignore-schedule` keep the global back-off.
  Caveat: the join is by basename, so two phases whose `command[0]` share a basename
  (e.g. two wrapper scripts both named `run`) are treated as one agent.

## Upgrading agent-runner

`upgrade` detects the deployment topology and takes the safe path for it. Pick
by how your service runs:

| Your deployment | How to upgrade | What happens |
|---|---|---|
| systemd `--user` (installed via `agent-runner install`) | `agent-runner upgrade [--target X.Y.Z]` | Full auto: graceful stop → pip → smoke → start, auto-rollback on smoke failure |
| systemd **system** unit / self-managed supervisor | `agent-runner upgrade --no-restart` then restart yourself | Package-only: pip + smoke (no service touched); you run `sudo systemctl restart <unit>` |
| container / pipx / fully hand-managed | `pip install --upgrade cli-agent-runner` then restart | Manual: you own both the install and the restart |

Whichever path: a long-running supervisor only loads the new code **after it
restarts** — that's why every non-`--user` path ends in a restart you run. The
three paths are detailed below.

Before touching the package or service, `upgrade` migrates your
`agent-runner.toml` to the current form: fields renamed or removed in an earlier
release are rewritten in place, the original is kept as a `.bak`, and a
`config_migrated` event records what changed. A config that needs a change
`upgrade` cannot make automatically aborts the run **before** it stops the
service or installs anything — fix it (or run `agent-runner migrate`) and retry.
Pass `--no-migrate` to skip this step, or run `agent-runner migrate` standalone
at any time (see [commands.md](commands.md) § "agent-runner migrate").

### Path 1 — systemd --user service (installed via `agent-runner install`)

    agent-runner upgrade --target <version>

Does stop → pip → smoke → start, with auto-rollback on smoke failure.

`--target` defaults to the latest version on PyPI. To pin a specific
version (or roll back), pass `--target X.Y.Z`.

### Path 2 — self-managed service (systemd system unit, foreground, etc.)

`agent-runner upgrade` detects it does not manage your service and does a
package-only upgrade (pip + smoke + rollback), then prints the restart command.
It never runs `sudo` and never starts a service it didn't install. Restart your
supervisor yourself:

    python3 -m pip install --user --break-system-packages --upgrade cli-agent-runner==<version>
    agent-runner --version
    sudo systemctl restart <your-unit>

> Do NOT run `agent-runner start` on a system-unit host — it spawns a second
> supervisor next to the one systemd manages.

Use `--no-restart` to force package-only mode even on a systemd --user host
(upgrade the package now, restart later):

    agent-runner upgrade --target <version> --no-restart

### What the smoke covers

The orchestrated path smokes `--version` + `peek --json --config <your toml>` in
a fresh subprocess — the new code does parse and validate your config — and a
failure auto-rolls-back (reinstall previous version, sanity smoke, restart). The
package-only path smokes `--version` only: your TOML is never loaded, and its
rollback restores the *package*, not a service it does not manage. Neither path
runs a round or invokes hooks/detectors, and plugins that fail to import surface
as a `UserWarning` with exit 0. Before restarting a 24/7 service onto a breaking
version, self-check with the service stopped:

    agent-runner peek --json --config <path> >/dev/null && echo "config loads"
    python3 -c "import my_plugin"            # each plugin you ship
    agent-runner serve --once --config <path>   # one real round (spawns the agent)

`peek` runs the full `load_config` path, so a config the new version rejects
fails here instead of on your first live round. (Calling `load_config` directly
takes a `Path`, not a `str` — `load_config(Path("..."))`.)

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
| Stop is stuck (user mode) | `agent-runner kill` → manual `pip install --upgrade ...` → `agent-runner start` |
| pip install fails (network / no PyPI) | Orchestrated: service left stopped, run `agent-runner start`. Package-only: service untouched, retry upgrade later. |
| Smoke fails, rollback succeeds | Orchestrated: service running on previous version. Package-only: on-disk package restored. |
| Smoke fails, rollback ALSO fails (rare) | Orchestrated: `service_upgrade_rollback_failed` event (service stopped). Manually: `pip install --force-reinstall cli-agent-runner==<known-good>` then `systemctl restart agent-runner@<project>`. |

### Postmortem trail

Grep events.jsonl for upgrade history:
```
grep -E "service_upgrad|package_upgraded|upgrade_start_failed" {log_dir}/events-*.jsonl | jq .
```
Event kinds:
- `service_upgraded` — clean orchestrated upgrade (live service on new version)
- `package_upgraded` — package updated, restart deferred to operator
- `service_upgrade_rolled_back` — attempted upgrade reverted (safety net fired)
- `service_upgrade_rollback_failed` — critical: needs manual intervention
- `upgrade_start_failed` — new code installed and smoke-passed, but the service did not start; run the `remedy` command in the payload

## Plugin cold-start (serve-startup hooks)

Plugins may register `ServeStartupHook` callbacks that fire once per
`agent-runner serve` invocation. The hook receives the loaded `Config` and
returns nothing.

Typical use case: seed a file or external state that subsequent rounds depend
on. Example: a plugin's `PreRoundHook` overwrites `/tmp/my-prompt.md` per
round, but the first round needs the file to already exist. A serve-startup
hook seeds it before any round runs.

### Failure behavior

If a serve-startup hook raises, `agent-runner serve` aborts with exit code 78
(deterministic — stays stopped, no restart) before entering the round loop. A
`serve_startup_hook_failed` event is emitted best-effort with payload
`{hook, exc_type, exc_msg}`.

To inspect failures: `grep serve_startup_hook_failed {log_dir}/events-*.jsonl`.

Operators can disable a misbehaving hook via `[plugins] disable = ["hook_name"]`
just like any other plugin component.

## Remote event relay & SSH trust

Remote support is an **event relay**, not remote detection:

```bash
agent-runner monitor --host pi --mode events
```

One command replaces the hand-rolled reconnect loop
(`while true; ssh pi 'agent-runner events --tail …'; sleep 30; done`). It spawns
`ssh pi -- agent-runner events --tail --kind <kinds>` and passes the remote's
JSON Lines through your stdout unchanged, and it owns the three things the
hand-rolled loop gets wrong:

- **No gap on reconnect.** The relay remembers the last `ts` it saw and
  reconnects with `--since <that ts>`, so events written while the link was down
  are replayed. At-least-once: the boundary event may repeat, none is lost. The
  first connect passes no `--since` — streaming starts from now.
- **A deadline, not an infinite retry.** Each ssh exit emits a
  `monitor_remote_blip` and retries with escalating backoff (1s → 2s → … → 30s).
  If the link stays down past `[monitor] remote_failure_tolerance_s`
  the relay emits `monitor_remote_giveup`, prints the last ssh diagnostic
  to stderr and exits 1 — so a service manager restarts it instead of a dead
  loop pretending to watch. The failure clock resets on the first relayed
  *line*, not on a successful connect. ssh's own stderr never reaches stdout;
  stdout is JSONL and nothing else.
- **No orphan process tree.** ssh runs in its own process group, and the whole
  group is torn down (SIGTERM → grace → SIGKILL) on Ctrl-C, on give-up and
  before every reconnect — including the `sleep` children a `pkill -f ssh`
  leaves behind.

Flags:

- `--kind K[,K2,…]` — kinds to relay. Default: every kind this client knows
  (built-ins plus locally installed plugin kinds). A kind that exists only on
  the remote must be named explicitly.
- `--remote-config PATH` — config path **on the remote host**. Omitted by
  default, so the remote resolves `./agent-runner.toml` in the ssh landing
  directory.

`--config` (the client's own config) is still required: it names the log dir
where the relay writes `monitor_remote_blip` / `monitor_remote_giveup`. Those
are the *client's* telemetry about its own link — they say nothing about the
supervised project's health, which is why they land locally.

### Detection stays on the host

`--host` with `--mode anomaly`, `narrate` or `http` exits 1. This is by design,
not a gap: the detectors read the supervised host's round logs and metrics, and
`auto_stop_on` stops that host's service. Running them there means detection and
auto-stop keep working with the laptop closed, the VPN down, or the ssh session
dead — nothing depends on a client being connected. Run:

    ssh pi
    agent-runner monitor --config ~/.agent-runner/<project>/agent-runner.toml

or install it as a unit (`agent-runner install --monitor`), and use the relay
from your laptop to watch what it emits.

The stop itself is local too. When it fails (unit missing, permission denied,
stale pidfile) the monitor emits `monitor_auto_stop_failed` with the error and
keeps polling — losing the supervision that noticed the problem would be worse
than a failed stop. Grep that kind on the *supervised* host, not on a relay
client.

### SSH trust boundary

Driving agent-runner over ssh (running the monitor on the host, `agent-runner
stop` from a laptop, deploys) is plain SSH, not a privileged API:

- It reads `~/.ssh/config` for the alias (host, user, identity file,
  `StrictHostKeyChecking` policy).
- Anything you run remotely runs with that account's full shell access —
  `agent-runner stop` on a remote service is a real state change.
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
- **Audit `auto_stop` triggers**: a monitor stopping a service is a real state
  change. Verify the detector logic and thresholds before enabling `auto_stop`
  on a production host.

### Liveness monitoring: the host-death blind spot

`agent-runner monitor` detects anomalies including `supervisor_stale` — the
supervisor stopped emitting events because it is stuck between rounds or dead.
But a monitor running on the *same host* as the supervisor dies when that host
dies, so it cannot report its own host's death.

That is the coverage boundary: agent-runner's detectors catch a supervisor stuck
on a live host (`supervisor_stale`, events frozen) but not the death of the host
itself. The event relay (see above) narrows it from a second machine —
`monitor --host <alias> --mode events` emits `monitor_remote_giveup` and exits 1
when the link stays down, which is a signal a service manager or alerting rule
can act on. It cannot distinguish a dead host from a dead network, so pair it
with an uptime/ping check, or a scheduled `ssh <host> agent-runner peek --json`
that alerts when the command or its `last_event_ts` goes stale.

<!-- authored: derived supervisor_stale default; SSOT agent_runner/config.py -->
The `supervisor_stale` threshold defaults to `round_timeout_s * 1.5`. Override
with `[monitor] supervisor_stale_threshold_s = N` for projects whose legitimate
cadence — very short rounds with occasional long legitimate gaps, or phase
overrides that raise `round_timeout_s` — does not fit the derived threshold. Set
to `0` to disable the detector entirely.

## Live event stream (machine-readable)

For machine consumption (parity comparisons, custom dashboards, automation
scripts), use:

```
agent-runner monitor --mode events --config /path/to/agent-runner.toml
```

Stdout emits one event per line as JSON. Subscription begins at process-start;
historical events are not replayed (use `cat events-*.jsonl | jq .` for that).
The mode follows monthly file rotation transparently.

To consume the same stream from another machine, add `--host` — the relay in
§ "Remote event relay & SSH trust" produces identical stdout.

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

The same family naming applies to the agent's own transcripts in
`{log_dir}/rounds/R<N>-<timestamp>.log`, one file per round.

<!-- authored: canonical round_log_retention=0 default; SSOT agent_runner/config.py -->
**Neither family is pruned by default (0.2.6+).** `runtime.round_log_retention`
defaults to `0`, which means never prune; both families grow for as long as the
deployment runs. That growth is watched by `disk_warning` (90%) and
`disk_critical` (95%, auto-stops the service by default), so a filling disk
announces itself long before it bites. Set a positive count to opt in —
`round-<N>.log` is then pruned by mtime at each serve startup and
`rounds/R<N>-*.log` by round number at the start of every round. See
`docs/configuration.md` § `runtime.round_log_retention` for sizing.

To reclaim space without enabling pruning, delete or archive old files under
`{log_dir}` / `{log_dir}/rounds` yourself; nothing in the supervisor depends on
their presence.

### `round_logs_prune_deferred` — a bulk prune was refused

Only reachable when you have opted into pruning. A prune that would delete more
files than it keeps is a **bulk** prune, and agent-runner never performs one:
it deletes nothing and emits `round_logs_prune_deferred` instead, with
`directory`, `existing` (files present), `keep` (current retention),
`would_delete` and a `hint`. The round — or the serve startup — proceeds
normally; only the deletion is deferred.

Expect it the first time you set a retention on an existing backlog, or right
after lowering the value. It repeats on every prune attempt until you resolve
it, either way:

```bash
agent-runner events --kind round_logs_prune_deferred   # directory + counts
```

- **Keep the backlog** — raise `[runtime] round_log_retention` to at least the
  reported `existing` (or back to `0`, never prune). Pruning resumes normally
  as the count grows past a positive value.
- **Drop it** — delete (or archive) files under the reported `directory`
  yourself; the next prune sees a count it no longer calls bulk and resumes.

Until then the family grows unbounded. Disk is usually the cheaper side of
that trade: these transcripts are what you reconstruct a lost round from.

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
2. High-level narrative (last 50 lines of `runtime.narrative_file`, default `log_dir/narrative.md`) <!-- authored: default narrative_file path; SSOT agent_runner/config.py -->
3. Recent events (last 20)
4. Round stdout/stderr tail (last 50 lines)
5. Self-termination flag status

JSON endpoint at `/api/state` for scripts.

Local-only (binds 127.0.0.1, no auth); `--host` is rejected. To watch from
another machine, use the event relay (`--mode events --host <alias>`) or forward
the port over ssh. Zero new dependencies — stdlib `http.server`.

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
agent-runner install --config <toml>
systemctl --user start agent-runner@<project>
systemctl --user enable agent-runner@<project>  # restart on Pi reboot
```

## Supervision coverage on a 24/7 host

Check this table before wrapping `[agent] command` in a shell script — most
host-safety needs are already in-core. Detector rows require a running
`agent-runner monitor` (`agent-runner install --monitor`); the rest are always on.

| Operator need | What exists | Notes |
|---|---|---|
| Never two supervisors on one project | `flock_concurrency` defense — exclusive `flock` on `{log_dir}/agent-runner.lock` | A second `serve` fails fast naming the holder's PID, lock age, and cmdline. No cron-overlap guard of your own needed |
| Runaway round | `[runtime] round_timeout_s` (default 1800) <!-- authored: default round_timeout_s; SSOT agent_runner/config.py --> | Wall-clock kill, emits `round_timeout_kill`. For a CLI with no self-timeout (pi has no turn cap, runtime timeout, or token budget) this is the **only** brake. Per-phase override: `[phases.<name>] round_timeout_s` |
| Cap the agent process itself | `[agent.env]` | Passed verbatim into the round subprocess env and takes precedence over the inherited `os.environ`, so e.g. `NODE_OPTIONS = "--max-old-space-size=384"` reaches a Node-based CLI |
| Host memory pressure | `mem_pressure` detector | Fires when host-wide `mem_available_mb` < `[monitor.host_health] mem_avail_min_mb` (default 200). Warning severity, **notify-only — it never auto-stops**. Whole-host figure, not the agent's share <!-- authored: default mem_avail_min_mb; SSOT agent_runner/config.py --> |
| Disk filling up | `disk_warning` at ≥90%, `disk_critical` at ≥95% | Sampled on `log_dir`'s partition. `disk_critical` auto-stops the service by default; `disk_warning` only alerts. Thresholds under `[monitor.host_health]` <!-- authored: disk_critical ships in the default auto_stop_on set; SSOT agent_runner/config.py --> |
| Auth burn (401 loop) | `oauth_fail` detector, auto-stops by default | Fires at ≥20% of a **fixed 10-event window** of `agent_exit` records. Under 10 exits it cannot fire at all, so a host that starts with bad credentials burns its first ~10 rounds before the stop lands. Two evidence paths: a round carrying `agent_auth_error_detected` (a plugin read the failure out of the CLI's own structured output — this is how pi's 401 becomes visible despite pi exiting 0), or the log-tail text heuristic, which still requires a **nonzero** agent exit as its false-positive shield |
| Token / cost accounting | `agent_usage_recorded` events in `events-*.jsonl` | Raw per-round records; rollups and budget alerts are the consumer's job. Emitted by the `claude_error_detector` / `gemini_error_detector` / `codewhale_error_detector` / `pi_error_detector` plugins. kimi's plugin classifies transient errors but emits no usage, because the Kimi Code CLI exposes no token counters in its stream-json output, so kimi rounds produce no usage events |

**Not covered: per-round peak RSS and pre-round memory gating.** agent-runner
records RSS nowhere; `metrics-*.jsonl` samples host memory once at round start and
once at round end, so a mid-round spike is invisible and nothing attributes memory
to the agent versus the rest of the host. There is likewise no "skip this round if
free memory < N" gate: a `PreRoundHook` does run before dispatch, but its return
value is ignored by contract, so it cannot veto a round — raising from it only
emits `hook_failed` and the round proceeds anyway. A wrapper script around
`[agent] command` that checks free memory and exits early remains the answer today.

## Troubleshooting

### Serve stopped on its own (`crash_loop` / `config_broken`)

**Symptom:** `serve` exited with a give-up code (`config_broken` → 78,
`crash_loop` → 75) but did little or no work. Two always-on defenses stop the loop
rather than respawn a doomed round forever — and because those codes are in the
unit's `RestartPreventExitStatus`, systemd `Restart=on-failure` does **not** bring
it back (the unit shows *failed*); intervention is needed.

| Event | Trigger | Fix |
|---|---|---|
| `config_broken` | Startup battery failed permanently — broken config (missing/short prompt, non-git `work_dir`, agent CLI not on PATH). The round exits `78`. | Read the round's `smoke_check_failed` event, fix the config, `agent-runner start`. |
| `crash_loop` | 5 consecutive *unknown* short crashes (non-zero exit < 60s, no classified transient); the delay escalates first. The `reason` field carries a redacted log tail. | Inspect the captured `reason` / round log, fix the root cause, `agent-runner start`. |

Recoverable-slow failures (rate-limit / 5h quota / 5xx / timeout) are classified
as transient errors and ride the back-off instead — they never trip `crash_loop`.

**Diagnose:**

```bash
agent-runner peek --round latest --log | tail -40
grep -E '"event": "(crash_loop|config_broken)"' <log_dir>/events-*.jsonl
```

### OAuth / auth failures (agent rejects requests)

**Symptom:** `monitor` reports `[CRIT] oauth_fail — N/10 recent rounds failed auth`.
The service auto-stops by default.

**Diagnose:**

```bash
agent-runner peek --round latest --log | tail -30   # look for 401 / unauthorized
agent-runner events --kind agent_auth_error_detected --window 20   # agent-reported 401s
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

When the event relay's ssh link or an agent round hits network errors, three
structured events serve as the index into deeper diagnostic logs. The two relay
events are written to the *client's* log dir (the machine running
`monitor --host`), not the supervised host's:

| Event | What it tells you | Where to look next |
|---|---|---|
| `monitor_remote_blip` | The event relay's ssh exited and is reconnecting (payload: `returncode`, ssh `error` tail, `resume_since`) | Subsequent events in the same window; if a `monitor_remote_giveup` follows, the relay exited |
| `monitor_remote_giveup` | The relay's link stayed down past `remote_failure_tolerance_s`; it exited 1 | The relay's own service manager for the restart; the host itself for why ssh stopped answering |
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

### Grace-kill and backgrounded work (`max_grace_after_result_s`)

Grace-kill is now process-group-liveness-aware. At grace expiry, agent-runner
inspects the agent's process group for live (non-zombie) worker processes and
takes one of three paths:

- **`round_grace_extended`** — grace elapsed but a live worker is still running
  (e.g. a backgrounded build). Round is NOT killed; agent-runner waits until the
  round finishes or hits the `round_timeout_s` wall-clock ceiling.
- **`round_grace_kill`** — grace elapsed and the process group is idle (genuine
  hang). Round is reaped, same as pre-0.1.38.
- **`round_timeout_kill`** — `round_timeout_s` wall-clock exceeded (hard ceiling,
  fires regardless of process-group state).

If you see repeated `round_grace_extended` events, the agent is backgrounding
work past `type=result`. Check the `live_children` field in the event to identify
the process; consider restructuring the agent to emit `type=result` only when
truly done.

**Persistent-helper exclusion (0.1.39+):** when an agent CLI keeps long-lived
helper subprocesses alive past `type=result` (claude does this with a Bash-tool
shell-snapshot), they would otherwise count as "live workers" and defer every
post-result hang to `round_timeout_s`. Set `[runtime] grace_kill_ignore_patterns
= [<regex>, ...]` to exclude them; the `claude` preset ships a default. The
`round_grace_extended` event's `ignored_children` field shows which cmdlines
matched a pattern.

**Reaping background workers:** if an agent needs to clean up its own
backgrounded helper processes, use process-group-aware means (its own
`bg`/job-control tooling, or killing by recorded PID/pgid) — not
`pkill -f <pattern>`, which matches on command-line substrings and can
self-match the agent's own process or hit unrelated siblings.

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
- `api_transient_5xx` — server outage (500/502/503/504/529). 60s default.
- `api_timeout` — 408 timeout. 30s default.

<!-- authored: default transient_error_action; SSOT agent_runner/config.py -->
**Default behavior (`transient_error_action = "back_off"`):**

The supervisor sleeps until `reset_at_epoch` (plus a 5–30s jitter),
then emits `transient_error_recovered` and resumes automatically. No
operator action needed during back-off.

**Under `phase_policy = "skip"` (0.2.10; agent-keyed since 0.2.11):** a throttle
does not sleep the loop — the rotation steps past every phase whose **agent** (the
basename of its `command[0]`) is throttled to a healthy sibling, and the global
back-off above applies only when every candidate phase is throttled or
window-closed. `wait` and `--ignore-schedule` keep the back-off behavior described
here.

**Forcing immediate stop instead:**

```toml
# agent-runner.toml
[runtime]
transient_error_action = "stop"   # 0.1.23+ canonical name
```

This causes the supervisor to emit `agent_self_terminated` with
`reason = "transient_error"` and exit cleanly. Restart with
`agent-runner start` after the underlying issue resolves.

**Checking throttle status:**

```bash
agent-runner peek --json | python3 -m json.tool | grep -A5 rate_limit
# "rate_limit": null  → not throttled
# "rate_limit": { "throttled_until_epoch": ..., "phase": "deepseek" }  → throttled
#   (phase is "" when the config has no [phases])
```

**Monitor alert:**

The `rate_limit_active` detector fires a `warning`-severity alert while
throttled (for any classification). It clears automatically when
`transient_error_recovered` is emitted. No configuration needed;
auto-stop is NOT triggered.

## 中文摘要

故障手册按场景：OAuth/auth 401（自动停服 → 刷新对应 provider 凭据，例如
claude 用 `claude /login`、aider 用 `export OPENAI_API_KEY=...` 后 `start`）；
网络抖（仅报警，自愈）；orphan stash 抢救（**用 SHA 不要用 stash@{N}**）；
服务启不来（看 journalctl 找 STARTUP FAIL）；卡轮 → `kill`；磁盘 95%
自动停服 → 清理日志后 `start`。
