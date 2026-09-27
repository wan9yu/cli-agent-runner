> 中文 · **[English](README.md)**

# RSI-Exam — 密封文件只打一次分

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `visible.py` 和 `prompts/` 拷进一个新的 git 仓库。不要把 `exam.py`、
   `sealed.txt`、`score.txt` 或 `delay.py` 拷进那个仓库。
2. 把 `exam.py`、`sealed.txt` 和 `score.txt` 放在仓库旁边。`score.txt`
   在 delay 脚本写入之前必须仍是字节 `PLANTED` 加一个换行。`sealed.txt`
   不是 prompt 文件。考卷是本地 Python 文件。它不是 LLM，也不是
   `[[goal.checks]]` 的一条。
3. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。把 `WORK` 和
   `PI` 换成仓库路径和 pi 二进制。不要设置 `stop_file`。不要把考卷、密封
   文件或 `score.txt` 加进 `[prompt] files`。
4. 把 `delay.py` 拷到仓库旁边。在这份配方目录里运行
   `python3 -m py_compile delay.py`，再运行 `python3 delay.py --self-check`。
   它会打印 `SELF_CHECK_OK` 和 `not_llm=1`。它不改 `score.txt`。在 Linux
   上还会打印 `proc_create_time_match=1`。
5. 先启动 delay 脚本，再启动一次 serve：

```bash
RSI_EXAM_WORK=<仓库> RSI_EXAM_CFG=<旁边目录> python3 delay.py
agent-runner --config <agent-runner.toml 的路径> serve
```

`prompts/program.md` 让孩子改 `visible.py`，并运行一次 `python3 visible.py`。
它不点名考卷路径，也不含 `SEALED_ASSERT_7c1e9a`。不要把这些字节写进 prompt。

## delay 脚本写什么

`delay.py` 是唯一的外层写入者。它不启动 serve，不循环 `agent-runner round`，
也不在 78、75 或 70 上再起。

它读 JSON `logs/serve.pid`，`{"pid": <int>, "create_time": <number>}`。
文件里只有数字，不授权写入。在 Linux 上，它用 `/proc/<pid>/stat` 第 22
字段和 `/proc/stat` 的 `btime` 对照，相差要小于 1 秒。它不用
`ps -o lstart=`。

第 1 轮不跑考卷。第 2 轮 `round_end` 之后、60 秒 delay 内、下一轮
`round_start` 之前，它加载 `exam.py` 一次，把那个浮点写进 `score.txt`。
它不把考卷拷进下一轮 prompt，也不写进 ledger。晚写就不是这份配方。

可见检查可以仍是红。不要加轮去等它变绿。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl` 和 delay 日志。退出码 0
只表示 serve 停了。

- 两轮都有可见 `goal_check`。`goal_check` 可能没有 `round_num`；把它归到
  包围它的 `round_start` 和 `round_end`。检查命令仍是 `python3 visible.py`。
- 第 2 轮开始前的 delay 日志里，可见源哈希和启动前的哈希不同。这就是
  在可见集上迭代。哈希没变，就不是这次落地。不要加轮，也不要再起一次 serve。
- `logs/rounds/R1-*.log` 和 `R2-*.log` 里的 user prompt 不含考卷路径，也不含
  `SEALED_ASSERT_7c1e9a`。封印只覆盖这些 prompt 字节。记下成绩单有没有提到
  仓库外路径。工具读了仓库外路径，本身不算这次落地失败。
- delay 日志只有一行 `decision=write exam_ran=1 once=1 in_delay=1`，并且
  成绩哈希和种下的哈希不同。再有一行 `decision=write` 就不是这份配方。
  不要再起一次 serve。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/program.md`，然后是 `logs/lessons.md`。
  考卷、密封文件和成绩文件都不是 prompt 路径。
- `restart_delay_s = 60`。`max_rounds = 2`。`round_budget_s = 1800`
  是每轮墙钟。
- `dirty_action = "ignore"`，所以对 `visible.py` 的修改会留到第 2 轮。
  `stash` 会把那次修改清掉。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
- 这份配方不创建 `stop_file`。
