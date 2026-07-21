# agent-runner — Documentation

A restart-on-exit supervisor for autonomous coding CLIs (Claude Code, aider,
gemini, codewhale, kimi, pi, or any prompt-arg CLI). Each round runs the agent once and
exits; an external
service manager (systemd / launchd / bash loop) restarts. State persists
across restarts via JSON files; defenses (timeout, process-group reap,
orphan-stash, smoke-check, monitor auto-stop) catch the recurring failure
modes.

## Reading order

| Page | Purpose |
|---|---|
| [quickstart.md](quickstart.md) | 5-minute install + first round |
| [commands.md](commands.md) | Full CLI verb reference |
| [configuration.md](configuration.md) | `agent-runner.toml` schema |
| [runbook.md](runbook.md) | Operator runbook + troubleshooting |
| [architecture.md](architecture.md) | Three-layer model + defense catalog |
| [events.md](events.md) | Event-kind catalog + JSONL schema |
| [plugins.md](plugins.md) | Plugin-author reference — hooks, detectors, contracts |
| [thesis.md](thesis.md) | Explicit non-goals — what agent-runner is NOT |
| [long-running-agents.md](long-running-agents.md) | Context rot, fresh eyes, long-lineage runs |
| [recipes/aider.md](recipes/aider.md) | aider walkthrough |
| [recipes/codewhale.md](recipes/codewhale.md) | codewhale walkthrough |
| [recipes/kimi.md](recipes/kimi.md) | Kimi Code CLI + running Kimi K3 via the claude preset |

## 中文导读

agent-runner 是一个面向自主编码 CLI（Claude Code、aider、gemini、codewhale、kimi、pi，
或任何 prompt-arg CLI）的「跑完即退、自动重启」调度框架。
进程退出后由外部服务管理器（systemd / launchd / bash loop）立即拉起新一轮，
状态文件持久化在 JSON 中，关键防御（超时、进程组回收、孤儿 stash、启动 smoke
检查、monitor 自动停服）都已 codify。

阅读顺序：先看 [quickstart.md](quickstart.md) 走通一遍，然后按需查
[commands.md](commands.md) / [configuration.md](configuration.md) /
[runbook.md](runbook.md)，想理解整体架构看 [architecture.md](architecture.md)，
写插件看 [plugins.md](plugins.md)。
