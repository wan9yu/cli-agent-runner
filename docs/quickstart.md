# Quickstart

Get one project running with `agent-runner` in five steps. Assumes Debian/Ubuntu
with `python3.11+`, `git`, and an agent CLI on PATH (e.g. `claude` or `aider`).

## 1. Install agent-runner

Install the package — the PyPI distribution name is `cli-agent-runner` and the
installed CLI command is `agent-runner`:

```bash
pip install cli-agent-runner
```
<!-- skip-test: installs the real published PyPI package; unsafe/pointless in a test run -->

On systems that block `pip install` into the system Python (Debian/Ubuntu PEP 668),
use `pipx` (recommended for CLI tools) or a venv:

```bash
pipx install cli-agent-runner
# OR
python3 -m venv ~/.agent-runner-venv
~/.agent-runner-venv/bin/pip install cli-agent-runner
export PATH="$HOME/.agent-runner-venv/bin:$PATH"
```
<!-- skip-test: installs the real published PyPI package; unsafe/pointless in a test run -->

## 2. Initialise your project

```bash
cd ~/myproject               # must be a git repo
agent-runner init            # writes agent-runner.toml + prompts/main.md + .gitignore
```
<!-- skip-test: illustrative fixed path (~/myproject); the git-init + `init --no-commit` block below (with an assert) is this same call, exercised for real in tmp_path -->

Edit `prompts/main.md` to describe what the agent should do per round.
Edit `agent-runner.toml` if you need to change `round_timeout_s` or `[phases]`.

The default preset (`--preset claude`) invokes `claude`. Other built-in
presets: `--preset aider`, `--preset gemini`, `--preset codewhale`, `--preset kimi`, and `--preset pi`. To use any other CLI,
edit `agent.command` to your CLI's invocation and `agent.prompt_arg_template`
to its prompt-argument syntax — for example:

```toml
[agent]
command = ["your-cli", "--flag1", "--flag2"]
prompt_arg_template = ["--prompt", "{prompt}"]
```

> **Using aider instead?** Run `agent-runner init --preset aider`.
> See [docs/recipes/aider.md](recipes/aider.md) for prereqs and the full preset.

Verify your scaffolding succeeded in a fresh repo:

```bash
git init -q -b main && echo init > README.md && git -c commit.gpgsign=false add . && git -c commit.gpgsign=false commit -q -m init
agent-runner init --no-commit
```
<!-- assert: agent-runner.toml -->

## 3. Run one round manually

```bash
agent-runner round
```
<!-- skip-test: needs a real agent CLI (claude by default) with live credentials; no API access in the test run -->

Expect the agent (Claude by default) to start, run, commit, and exit. Logs
land in `~/.agent-runner/<project>/logs/`.

## 4. Install as a systemd user service

```bash
agent-runner install --monitor
```
<!-- skip-test: writes + starts real systemd user units; unsafe to run against the test host -->

This writes two systemd units (`agent-runner@<project>.service` and
`agent-runner-monitor@<project>.service`), enables them, and starts them.
The monitor sidekick auto-stops the service if it sees OAuth failures or
disk-full conditions.

## 5. Observe

```bash
agent-runner status                     # service state (pretty text; add --json for JSON)
agent-runner peek                       # current snapshot
agent-runner peek --select system       # drill into mem/disk/load
agent-runner watch                      # auto-refresh peek
agent-runner monitor                    # tail anomaly stream

journalctl --user -u agent-runner@myproject -f   # systemd logs
```
<!-- skip-test: reads a running service + journal this test never installed or started -->

To stop:

```bash
agent-runner stop          # graceful (waits for current round)
agent-runner kill          # force (5s grace then SIGKILL)
```
<!-- skip-test: targets a running service this test never installed or started -->

## 中文摘要

5 步搭通：`pip install cli-agent-runner` 装包（命令名仍是 `agent-runner`）→
`agent-runner init` 在你的 git repo 里生成 `agent-runner.toml` → `agent-runner round`
跑通一轮 → `agent-runner install --monitor` 装 systemd 服务（含 monitor 副服务）
→ `agent-runner peek / watch / monitor` 观察。停服两种语义：`stop`（优雅，
等当前轮）/ `kill`（强制）。
要换用 aider：`agent-runner init --preset aider`，详见 [docs/recipes/aider.md](recipes/aider.md)。
