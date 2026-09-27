> 中文 · **[English](README.md)**

# Ralph — 同一提示直到检查通过

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。共同的读法见
[怎么读一份配方](../README.zh.md#怎么读一份配方)。

## 步骤

1. 把 `lane.py`、`bonus.py`、`test_pair.py` 和 `prompts/` 拷进一个新的
   git 仓库。`lane.py` 和 `bonus.py` 保持原样，两个函数都返回 0。
   serve 之前不要把它们改对。
2. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`，
   和一份 `stop-watch.py` 放在一起。`--config` 打开的是
   `agent-runner.toml`，不是 example。
3. 在那份 toml 里，把 `WORK` 换成仓库路径，`PI` 换成 pi 二进制，`STOP`
   换成仓库旁边的一个路径。`STOP` 这时必须还不存在。它就是 `stop_file`。
4. 运行 `python3 -m py_compile stop-watch.py`，再运行
   `python3 stop-watch.py --self-check`。它会打印 `SELF_CHECK_OK`。
5. 先启动 watcher，再启动一次 serve：

```bash
RALPH_WORK=<仓库> RALPH_STOP=<STOP> python3 stop-watch.py
agent-runner --config <agent-runner.toml 的路径> serve
```

孩子每一轮只改一个源文件，再跑一次 `python3 -m unittest test_pair`，
套件失败也结束这一轮。`serve` 继续跑。watcher 只在 `round_end` 之后、
60 秒 delay 内创建 `STOP`，并且要同时满足：这一轮的
`goal_check.satisfied` 是 true，下一轮还没开始，`logs/serve.pid` 里的
pid 仍活着。检查为 false 时不创建这个文件。

`prompts/main.md` 是孩子的任务，不是你的步骤。两轮之间不要改它。
测试保持可读。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl`，搜索 `goal_check`。
要看到更早一行含 `"satisfied": false`，更晚一行含 `"satisfied": true`。
`prompts/main.md` 和 `test_pair.py` 应保持不变。`test_pair.py` 不应出现在
`dirty_detected.files` 里。检查命令仍应是 `python3 -m unittest test_pair`。

退出码 0 只表示 serve 停了。`stop_file_detected` 表示 watcher 创建了
`STOP`。`max_rounds_reached` 表示六轮结束，没有创建那个文件。

如果第 1 轮已经通过，就停。不要加轮去制造更早的一次失败。如果六轮结束
仍没有通过的检查，那就是结果。不要再起一次 serve。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/main.md`，然后是 `logs/lessons.md`。
  启动时 ledger 文件不在，会被跳过。这不是检查命令变了。
- `restart_delay_s = 60`。`round_budget_s = 1800` 是挂起时的墙钟。
  `max_rounds = 6`。
- `dirty_action = "ignore"`，所以第一次改的文件会留到第 2 轮。
  `stash` 会把那次修改清掉。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
