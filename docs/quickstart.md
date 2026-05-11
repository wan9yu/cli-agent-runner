# Quickstart

Get one project running with `agent-runner` in five steps. Assumes Debian/Ubuntu
with `python3.11+`, `git`, and the `claude` CLI on PATH.

## 1. Install agent-runner

```bash
git clone https://github.com/wan9yu/agent-runner.git ~/.agent-runner-pkg
cd ~/.agent-runner-pkg
python3 -m venv .venv
.venv/bin/pip install -e .
export PATH="$HOME/.agent-runner-pkg/.venv/bin:$PATH"
```

## 2. Initialise your project

```bash
cd ~/myproject               # must be a git repo
agent-runner init            # writes agent-runner.toml + prompts/main.md + .gitignore
```

Edit `prompts/main.md` to describe what the agent should do per round.
Edit `agent-runner.toml` if you need to change `round_timeout_s` or `[phases]`.

## 3. Run one round manually

```bash
agent-runner round
```

Expect a Claude session to start, run, commit, and exit. Logs land in
`~/.agent-runner/<project>/logs/`.

## 4. Install as a systemd user service

```bash
agent-runner install --monitor
```

This writes two systemd units (`agent-runner@<project>.service` and
`agent-runner-monitor@<project>.service`), enables them, and starts them.
The monitor sidekick auto-stops the service if it sees OAuth failures or
disk-full conditions.

## 5. Observe

```bash
agent-runner status                     # service state JSON
agent-runner peek                       # current snapshot
agent-runner peek --select system       # drill into mem/disk/load
agent-runner watch                      # auto-refresh peek
agent-runner monitor                    # tail anomaly stream

journalctl --user -u agent-runner@myproject -f   # systemd logs
```

To stop:

```bash
agent-runner stop          # graceful (waits for current round)
agent-runner kill          # force (5s grace then SIGKILL)
agent-runner cancel        # SIGINT to claude (best-effort wrap-up)
```

## 中文摘要

5 步搭通：`pip install -e .` → `agent-runner init` → `agent-runner round`
跑通一轮 → `agent-runner install --monitor` 装 systemd 服务（含 monitor 副服务）
→ `agent-runner peek / watch / monitor` 观察。停服三种语义：`stop`（优雅，
等当前轮）/ `kill`（强制）/ `cancel`（向 claude 发 SIGINT 提示收尾）。
