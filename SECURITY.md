# Security policy

## Supported versions

The latest tagged release. We do not back-port security fixes to older versions.

## Reporting a vulnerability

Please report security issues privately via GitHub Security Advisories:
<https://github.com/wan9yu/cli-agent-runner/security/advisories/new>

If GitHub Security Advisories is not workable for you, email
`wangyu@go2imagination.com` with the subject line `agent-runner security`.

Please **do not** open a public issue for security problems.

## Response timeline

- Acknowledgement within 3 business days.
- An initial assessment within 7 business days.
- A fix and coordinated disclosure timeline once the scope is understood.

## Threat model

agent-runner is a **supervisor**, not a sandbox. It provides observability
(structured events, peek/watch/monitor) and lifecycle safety (round timeouts,
process-group reaping, orphan stashing, auto-stop on critical alerts). It does
**not** confine the agent process: the agent runs with the invoking user's full
privileges and can read, write, and execute anything that user can.

Prompt injection from untrusted repository content is unsolved and out of the
supervisor's reach — a malicious file in the work tree can steer the agent, and
no defense in this project prevents that.

Operators supervising untrusted input should run the agent as a dedicated
unprivileged user, inside a container or VM, without passwordless sudo, and
with egress limits. The supervisor's defenses bound the blast radius of a
*misbehaving* agent, not a *hostile* one.

## Containment

If you need to bound what the agent can reach, run it inside a container (or
VM) you supply, via `[agent] exec_prefix` — see
[docs/recipes/container-pi.md](docs/recipes/container-pi.md). The container
(or VM) enforces the isolation; agent-runner still just watches the
resulting process from outside, the same way it watches a bare agent
process.

One limit worth knowing: agent-runner's own process-tree defenses (reaping a
hung round's descendants) only see processes in its own subtree, and a
container-runtime-managed process usually isn't one of them. On a stuck
round, the one remaining lever is stopping the container by the id
agent-runner captured at spawn, which only works for a plain `docker
run …`/`podman run …` invocation — not one wrapped in `sudo` or other global
flags (see container-pi.md's footguns). Using a container this way gives you
a narrower safety net than the host-level reaping a bare agent process gets,
not a wider one — keep running it as a dedicated unprivileged user with no
passwordless sudo and tight egress limits regardless.

**Residual: `agent-runner.toml` lives inside the mount.** The startup check
above stops the agent's pid and lock files from sitting inside a mounted
`work_dir` (the injected cidfile is separately placed in a host-private temp
dir outside any mount, so it's protected independently of `log_dir`) — but
`agent-runner.toml` itself is scaffolded at the `work_dir` root, and the
container recipes in
[docs/recipes/container-pi.md](docs/recipes/container-pi.md) bind-mount all
of `work_dir` read-write. Each round re-reads the config, so a containerized
agent that can write its own mount can rewrite `[agent] command` /
`exec_prefix` — or `runtime.stop_file`, if it's pointed inside the mount —
and have it run on the host next round. This is consistent with the threat
model above: agent-runner bounds a *misbehaving* agent, not a *hostile* one,
and containment is the container/VM's job, not agent-runner's. An operator
who needs to contain a not-fully-trusted agent should keep the config (and
`runtime.stop_file`) outside any bind-mount, or otherwise treat the
container boundary as their own responsibility to shape.

## Scope

In scope: the `agent_runner` Python package, its CLI, its bundled systemd
unit templates, and the CI / release workflows.

Out of scope: vulnerabilities in dependencies (psutil, etc.) — please
report those upstream. Misconfiguration of a downstream deployment is also
out of scope unless agent-runner's defaults caused it.
