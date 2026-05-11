# agent-runner

Restart-on-exit supervisor for autonomous CLI agents. Each round runs the agent
once and exits; an external wrapper (launchd / systemd / bash loop) restarts.

## Status

Early development. See `docs/` for design background.

## Install

```bash
git clone https://github.com/wan9yu/agent-runner.git
cd agent-runner
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick start

Copy `deploy/example-agent-runner.toml` to your project, edit `agent.command`
and `prompt.file`, then:

```bash
agent-runner round       # run one round
agent-runner --status    # show last round status
```

For continuous operation, install one of the wrappers in `deploy/`.
