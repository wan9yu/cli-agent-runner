> 中文 · **[English](README.md)**

# 串行丢弃 — 红的尝试不合并

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `attempt.py`、`notes.md` 和 `prompts/` 拷进一个新的 git 仓库。这个
   仓库就是尝试目录。不要把 `keep/` 或 `visible_check.py` 拷进去。
2. 把 `keep/` 放在仓库旁边。它是种下的保留树，不是 prompt 文件。把
   `visible_check.py` 也放在仓库旁边。它打印 `0.0`，退出码 1。它不读尝试
   目录，也不读保留目录。
3. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。启动前把 `WORK`
   换成尝试目录的绝对路径。相对路径会按配置文件所在目录解析。把 `PI` 和
   `CHECK` 换成 pi 二进制和 `visible_check.py` 的绝对路径。不要设置
   `stop_file`。不要设置 `auto_commit`。
4. 把 `delay.py` 拷到仓库旁边。在这份配方目录里运行
   `python3 -m py_compile delay.py`，再运行 `python3 delay.py --self-check`。
   它会打印 `SELF_CHECK_OK`、`no_merge=1` 和 `not_karpathy_restore=1`。
   它不改 `keep/`。这份 stdout 不是 serve 的轮间行。在 Linux 上还会打印
   `proc_create_time_match=1`。
5. 先启动 delay 脚本，再启动一次 serve。不要创建 worktree。

```bash
SERIAL_WORK=<尝试目录> SERIAL_CFG=<旁边目录> python3 delay.py
agent-runner --config <agent-runner.toml 的路径> serve
```

`prompts/program.md` 只让孩子改 `notes.md`，并让 `attempt.py` 保持不动。
它不含 `VALUE = 1.0`。

## delay 脚本写什么

`delay.py` 是唯一的外层写入者。它不启动 serve，不循环 `agent-runner round`，
也不在 78、75 或 70 上再起。

它读 JSON `logs/serve.pid`。裸整数不授权写入。在 Linux 上，它对照 `/proc`
启动时间，相差要小于 1 秒。它不用 `ps -o lstart=`。

serve 之前，delay 日志写下 `work_dir` 就是尝试目录。每一次红的 `round_end`
之后、60 秒 delay 内、下一轮 `round_start` 之前，它写下 `no_merge=1` 和
`not_karpathy_restore=1`。它不把尝试目录拷进保留树。它不把快照写回
`attempt.py`。`stash` 不是丢掉。它不为下一轮再开 worktree。

第 1 轮已经是绿，就不是这次落地。不要再起一次 serve 去补一次红。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl` 和 delay 日志。serve 结束后
的工作树不能代替 delay 日志里的行。退出码 0 只表示 serve 停了。

- 第 1 轮 `goal_check` 是红。没有 `round_num` 的检查，归到包围它的
  `round_start` 和 `round_end`。
- serve 之前的 delay 行有 `work_dir_is_attempt=1` 和 `toml_work_dir_equal=1`。
- 每一次红的轮间都有 `decision=no_merge no_merge=1 not_karpathy_restore=1
  keep_equals_planted=1 in_delay=1`，并且保留目录哈希仍等于种下的哈希。
  第 2 轮若也是红，同样要有这一行。
- `new_worktree=0`。检查命令仍是启动时设的旁边 `visible_check.py` 路径。

第 1 轮已经是绿，保留目录被写过，或尝试源被按指标写回，就不是这次落地。
不要再起一次 serve。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/program.md`，然后是 `logs/lessons.md`。
  保留目录不是 prompt 路径。
- `restart_delay_s = 60`。`max_rounds = 2`。`round_budget_s = 1800`
  是每轮墙钟。
- `dirty_action = "ignore"`。没有 `auto_commit`。没有 `stop_file`。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
