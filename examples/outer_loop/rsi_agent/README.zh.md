> 中文 · **[English](README.md)**

# RSIAgent — 外层队列给出下一题

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `actor.py` 和 `prompts/` 拷进一个新的 git 仓库。不要把 `course.md`
   或 `queue/` 拷进那个仓库。
2. 把 `course.md` 放在仓库旁边。它是第 1 轮课程，只有任务 A。把
   `queue/task-b.md` 也放在仓库旁边。它不是 prompt 路径。serve 之前，
   任务 B 只在这份队列文件里。
3. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。把 `WORK`、
   `PI`、`COURSE` 换成仓库路径、pi 二进制、旁边的课程文件。不要设置
   `stop_file`。
4. 把 `delay.py` 拷到仓库旁边。运行 `python3 -m py_compile delay.py`，再
   运行 `python3 delay.py --self-check`。它会打印 `SELF_CHECK_OK` 和
   `WRITE_ONCE round1=1 round2=0`。
5. 先启动 delay 脚本，再启动一次 serve：

```bash
RSI_WORK=<仓库> RSI_COURSE=<course.md> RSI_QUEUE=<queue/task-b.md> \
  python3 delay.py
agent-runner --config <agent-runner.toml 的路径> serve
```

`prompts/program.md` 是常设说明。它不点名任务 B。不要把课程文件的路径
写进任何一份交给孩子的文件。

## delay 脚本写什么

`delay.py` 在第 1 轮 `round_end` 之后、第 2 轮 `round_start` 之前，把队列
文件追加进课程文件一次。它用临时文件、fsync 和 `os.replace`。启动字节仍是
前缀。它不换 `files[0]`，不恢复 `actor.py`，不读助手文本。缺失浮点不阻止
这次写入。第 2 轮不再写。

它只在 `logs/serve.pid` 里的 pid 仍活着、并且还在 60 秒 delay 内时写入。
文件里只有数字，不授权写入。

阅卷仍是 `python3 actor.py`。这条命令打印一个有限浮点数。它不是 LLM。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl`、delay 日志和
`logs/rounds/R1-*.log`。

- 写入之前，课程哈希等于启动哈希。
- 第 1 轮 user prompt 含 `TASK_A_CURRICULUM`，不含 `TASK_B_CURRICULUM`，
  也不含 `MARK_B = 1`。孩子改的是 `actor.py`。
- delay 日志的 `decision=write` 早于第 2 轮 `round_start`。前缀哈希仍等于
  启动哈希，此时文件含任务 B。
- 第 2 轮不再写课程文件。
- 检查命令仍是 `python3 actor.py`。digest 变了是课程字节变了，不是换了阅卷。

任务 B 若已经在第 1 轮 prompt 里，就不是这份配方。不要加轮，也不要再起一次
serve。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/program.md`，然后是 `logs/lessons.md`，然后是
  `COURSE`。队列文件不是 prompt 路径。课程文件不是 ledger。
- `restart_delay_s = 60`。`max_rounds = 2`。`round_budget_s = 1800`
  是每轮墙钟。
- `dirty_action = "ignore"`，所以第 1 轮对 `actor.py` 的修改会留到第 2 轮。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
- 这份配方不创建 `stop_file`。
