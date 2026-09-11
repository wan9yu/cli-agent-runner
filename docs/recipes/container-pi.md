# Running pi inside a container

This is [pi.md](pi.md)'s agent, run inside a container you supply, via
`[agent] exec_prefix` (schema: [configuration.md](../configuration.md)).
`exec_prefix` is an opaque list spliced onto the front of `command` at spawn —
so the effective process is `docker run … my-pi-image pi --mode json`, and
agent-runner watches that `docker run` client as an ordinary process, exactly
as it would watch a bare `pi` process. The container (and the OS underneath
it) enforces the isolation; agent-runner does not sandbox the agent itself —
it runs the agent in a container you supply and watches from outside.

## Prerequisites

- Docker (or Podman — see the rootless delta below) installed on the
  supervisor host.
- Your user in the `docker` group — **not** `sudo` (see Footguns).
- An image you build yourself, e.g.:
  ```dockerfile
  FROM node:20-slim
  RUN npm i -g --ignore-scripts @earendil-works/pi-coding-agent
  # the template runs as -u 1000:1000 with HOME=/home/agent, so the image
  # must provide that dir owned by the UID (or $HOME-relative writes fail):
  RUN mkdir -p /home/agent && chown 1000:1000 /home/agent
  ```
- A provider/model configured for pi — same as [pi.md](pi.md)'s Provider auth
  section.
- A git repo as `work_dir`, bind-mounted into the container at `/work`.

## Config

```toml
[agent]
command = ["pi", "--mode", "json"]
exec_prefix = ["docker", "run", "--rm", "-i", "--init", "--stop-timeout", "5",
               "-u", "1000:1000", "-e", "HOME=/home/agent",
               "-v", "{work_dir}:/work:Z", "-w", "/work",
               "-e", "ANTHROPIC_API_KEY", "my-pi-image"]
```

`{work_dir}` is the one templated token — agent-runner substitutes it with
the run's absolute `work_dir` at spawn; every other token passes through
untouched. `command` stays `["pi", "--mode", "json"]`, so pi's identity
(throttle / crash-loop / usage-plugin keying, all keyed on `command[0]`) is
unaffected by running inside docker.

### Why each flag

- **`--rm`** — remove the container on exit. Without it, containers
  accumulate one per round.
- **`-i`** — keep stdin open. pi's `prompt_delivery = "stdin"` needs it;
  without `-i`, docker drops stdin and the agent hangs until
  `round_timeout_s` kills it. agent-runner checks this at startup and
  refuses to start a stdin-delivery config whose `exec_prefix` is missing
  `-i`.
- **`--init`** — run an init process as PID 1 inside the container, so
  SIGTERM reaches pi and in-container zombies get reaped. Without it, a
  shell-form entrypoint as PID 1 can ignore SIGTERM outright.
- **`--stop-timeout 5`** — bound `docker stop`'s grace period so teardown
  after a stop stays short and predictable.
- **`-u 1000:1000`** — run as your host UID, not root. A root-owned file
  written into the bind-mounted `work_dir` poisons agent-runner's host-side
  dirty-tree stash/commit, and trips git's "dubious ownership" check
  in-container. Use `id -u`/`id -g` if your UID isn't 1000.
- **`-e HOME=/home/agent`** — an arbitrary UID has no `/etc/passwd` entry, so
  `~` expansion breaks inside the container; set `HOME` explicitly.
- **`-v {work_dir}:/work:Z`** — bind-mount the run's `work_dir`. `:Z`
  relabels the mount for SELinux hosts (Fedora/RHEL); drop it elsewhere.
- **`-w /work`** — pi has no `--cwd` flag (see [pi.md](pi.md)), so the
  container's working directory has to already be the mount.
- **`-e ANTHROPIC_API_KEY`** (value-less) — forwards the key from
  agent-runner's own environment into the container. **Never write
  `-e ANTHROPIC_API_KEY=sk-…`** — a valued `-e` puts the secret straight into
  the `docker run` argv, which lands in `ps` output and `/proc/<pid>/cmdline`
  for any local user to read. Add every other provider key the same,
  value-less way.
- **`my-pi-image`** — the image you built above.

## Auth

Prefer env vars, as above — there's no state to lose when `--rm` removes the
container each round. If your provider needs a credential *file* (e.g.
`pi /login`'s `~/.pi/agent/auth.json`), mount it **outside** `work_dir` —
mounting it inside would show up as tracked-file churn to agent-runner's
dirty-tree detector every round:

```
-v /home/you/.pi-container-auth:/home/agent/.pi/agent:ro
```

## Footguns

These aren't enforced by agent-runner (`exec_prefix` is opaque) — get them
right yourself:

- **`sudo docker run` loses the clean stop.** agent-runner's container-orphan
  defense only injects a `--cidfile` (and so can attempt a clean `docker
  stop` on a stuck round) for the literal `docker run …` shape. A
  `sudo`-wrapped or globally-flagged form is still *detected* — you get the
  warning and event — but not covered by the clean stop. Put your user in
  the `docker` group instead of prefixing `sudo`.
- **Missing `--rm`** accumulates one container per round, forever.
- **`--network none`** breaks the agent outright — it can't reach the model
  API.
- **Missing `-u`** runs the container as root — see the root-owned-files
  footgun above.
- **`-d`/`--detach`** — never use it. Detached `docker run` exits 0
  immediately while the container is still starting, so agent-runner sees an
  instant "successful" round with no agent work actually done.
- **`-t` without a real TTY** fails fast — docker allocates a pseudo-TTY and
  errors when the supervisor's own stdin/stdout aren't one, which under a
  service manager they usually aren't.
- **`--sig-proxy=false`** disables the relay of agent-runner's SIGTERM into
  the container for cooperative shutdown. Leave it at its default (`true`).

## Not for tiny edge hosts

This recipe assumes a host with room for a container runtime on top of the
agent's own (non-trivial) memory footprint. It isn't a fit for a small edge
board — run pi bare there instead (see [pi.md](pi.md)).

## Deltas

### kimi — ENV-only, no mount

[kimi.md](kimi.md)'s CLI is fully configured over four env vars and needs no
file at all — prefer this shape whenever your CLI supports it, since there's
nothing to bind-mount:

```toml
[agent]
command = ["kimi", "--output-format", "stream-json"]
exec_prefix = ["docker", "run", "--rm", "-i", "--init", "--stop-timeout", "5",
               "-u", "1000:1000", "-e", "HOME=/home/agent",
               "-v", "{work_dir}:/work:Z", "-w", "/work",
               "-e", "KIMI_MODEL_NAME", "-e", "KIMI_MODEL_API_KEY",
               "-e", "KIMI_MODEL_BASE_URL", "-e", "KIMI_MODEL_PROVIDER_TYPE",
               "my-kimi-image"]
```

### claude — root rejection + config mount

Claude Code refuses `--dangerously-skip-permissions` when run as root, so
`-u` is mandatory here, not just recommended. If you authenticate via
`~/.claude` instead of an API key, mount it outside `work_dir` and point
`CLAUDE_CONFIG_DIR` at the mount:

```toml
exec_prefix = ["docker", "run", "--rm", "-i", "--init", "--stop-timeout", "5",
               "-u", "1000:1000", "-e", "HOME=/home/agent",
               "-v", "{work_dir}:/work:Z", "-w", "/work",
               "-v", "/home/you/.claude-container:/home/agent/.claude:ro",
               "-e", "CLAUDE_CONFIG_DIR=/home/agent/.claude",
               "my-claude-image"]
```

### rootless podman — `--userns=keep-id` instead of `-u`

Podman rootless already runs as your user; swap `-u 1000:1000` for
`--userns=keep-id`, which maps your host UID inside the container without
needing root at all:

```toml
exec_prefix = ["podman", "run", "--rm", "-i", "--init", "--stop-timeout", "5",
               "--userns=keep-id", "-e", "HOME=/home/agent",
               "-v", "{work_dir}:/work:Z", "-w", "/work",
               "-e", "ANTHROPIC_API_KEY", "my-pi-image"]
```

See also: [docs/configuration.md](../configuration.md),
[SECURITY.md](../../SECURITY.md#containment).
