# Running the whole unit in one container

This is a **recipe** — `deploy/Dockerfile` and `deploy/docker-compose.yml`
are templates to copy and adapt into your own project, not a published or
maintained image.

Where [container-pi.md](container-pi.md) boxes the *agent* and keeps the
supervisor on the host, this recipe does the opposite: the supervisor and
the agent CLI both run inside one container, and that container is the unit
you bound against the host (filesystem, network, resources). agent-runner
still supervises the agent CLI as an ordinary in-container subprocess — this
container doesn't add any isolation between the supervisor and the agent, it
only bounds the whole unit against the host it runs on.

## When to use this vs `exec_prefix`

- **`exec_prefix`** ([container-pi.md](container-pi.md)) — box the agent
  only; the supervisor keeps its normal host-level view (events, `peek`,
  host PSI/memory signals). Prefer this by default.
- **This recipe** — you want one image that *is* the deployable unit (e.g.
  shipping the whole thing to a host you don't otherwise manage). In
  exchange, the supervisor's view of memory/PSI pressure is now the
  container's own, not the host's.

## Files

- `deploy/Dockerfile` — `python:3.12-slim` + `pip install cli-agent-runner` +
  a commented-out slot for your agent CLI's install step.
- `deploy/docker-compose.yml` — builds the image, mounts your project (which
  should contain `agent-runner.toml`) at `/work`, runs `serve`.

## Use it

```bash
cp deploy/Dockerfile deploy/docker-compose.yml your-project/
cd your-project
# edit Dockerfile: uncomment/add your agent CLI's install step
# agent-runner.toml already exists at the project root (agent-runner init)
docker compose up -d
docker compose logs -f
```

## Not for tiny edge hosts

Same caveat as [container-pi.md](container-pi.md): don't reach for this on a
small edge board. It's a fat-host/homelab/server recipe.

See also: [docs/quickstart.md](../quickstart.md),
[SECURITY.md](../../SECURITY.md#containment).
