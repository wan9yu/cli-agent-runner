> 中文 · **[English](README.md)**

# 循环工程 — 完成检查不是孩子说的「做完了」

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `add.py`、`suite_add.py` 和 `prompts/` 拷进一个新的 git 仓库。
2. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。
3. 在那份 toml 里，把 `WORK` 换成仓库路径，`PI` 换成 pi 二进制。
4. 启动一次 serve：

```bash
agent-runner --config <agent-runner.toml 的路径> serve
```

`max_rounds` 是 2。同一次进程会进入第 2 轮，即使第 1 轮的检查失败。
不要再起一次 serve 去追通过的检查。那是 [Ralph](../ralph/README.zh.md)
的配方。

`prompts/main.md` 是孩子的任务。两轮之间不要改它。它只提到 `add(1, 2)`。
unittest 还要求 `add("1", 2)` 抛错。这份配方不是要把套件跑到通过。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl`。

第 1 轮应同时有这两件事：

- `logs/rounds/R1-*.log` 里，`agent_end` 且 `role == assistant` 的文本是
  `DONE`。同一条记录里的 user 文本不算。复述打印指令不算。
  `logs/round-1.log` 是操作日志，不是这份成绩单。
- 这一轮的 `goal_check.satisfied` 是 false。

第 2 轮开始，就是这次 serve 在继续。`prompts/main.md` 应保持不变。
第 1 轮 `config_changed: true` 是第一次记下 digest，不是有人改了 prompt。
比两轮 `round_start` 的 `config_digest`，或自己对 prompt 文件做哈希。

检查命令是 `python3 -m unittest suite_add.py`。孩子的 `DONE` 不是这个检查。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/main.md`，然后是 `logs/lessons.md`。
- `restart_delay_s = 60`。`round_budget_s = 1800` 是挂起时的墙钟。
  `max_rounds = 2`。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
