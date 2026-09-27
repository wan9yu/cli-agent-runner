> 中文 · **[English](README.md)**

# LongHorizon — 事实来自检查，不来自孩子

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `check_marker.py` 和 `prompts/` 拷进一个新的 git 仓库。不要把
   `snapshot.md` 拷进那个仓库。
2. 把一份 `snapshot.md` 放在仓库旁边。在同一目录创建空文件 `state.txt`。
3. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。把 `WORK` 换成
   仓库路径，`PI` 换成 pi 二进制，`SNAPSHOT` 换成旁边那份 `snapshot.md`
   的路径。不要设置 `stop_file`。
4. 把 `delay.py` 拷到仓库旁边。先启动它，再启动一次 serve：

```bash
LH_WORK=<仓库> LH_SNAPSHOT=<snapshot.md> LH_STATE=<state.txt> python3 delay.py
agent-runner --config <agent-runner.toml 的路径> serve
```

`prompts/main.md` 是孩子的任务。你只在 delay 里改它的最后一行
`Instruction:`。上面的文字保持不动。

## delay 脚本写什么

`delay.py` 从 `logs/events-YYYY-MM.jsonl` 读 `goal_check`。决定写什么时
不读助手文本。它只在 `logs/serve.pid` 里的 pid 仍活着、并且还在 60 秒
delay 内时写入。

检查为红时，它只做两处写入：

1. 把词 `retry` 写入 `snapshot.md`。
2. 只替换 `Instruction:` 那一行，让孩子在仓库根创建 `marker.txt`。

红的时候不追加 `state.txt`，也不创建 `stop_file`。

第一次变绿时，它只做两处写入：

1. 把 `value=<goal_check.value>` 追加到 `state.txt` 一次。这个值来自事件。
2. 把同一个值和词 `done` 写入 `snapshot.md`。

它不把 `done` 或这个值写进第 3 轮的 `Instruction:` 行。后面又绿的轮次
不再追加 `state.txt`。

这份配方不用 `blocked` 和 `ask`，也不创建 `stop_file`。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl`。

- 第 1 轮：`agent_end` 且 `role == assistant` 的消息含 `DONE`，这一轮的
  `goal_check.satisfied` 是 false，`state.txt` 没变。同一条记录里的
  user 文本不算。成绩单是 `logs/rounds/R1-*.log`。
- 第 2 轮：检查命令仍是 `python3 check_marker.py`，`marker.txt` 存在，
  `state.txt` 的新行等于该事件的 `value`。
- 第 3 轮：`Instruction:` 行里既没有 `done`，也没有那个值。

`dirty_action` 是 `ignore`，因为 instruction 行的修改和 `marker.txt`
必须留在仓库里给下一轮。`stash` 会把两者都清掉。

第 1 轮 `config_changed: true` 是第一次记下 digest。之后 digest 变，是
instruction 行或 `snapshot.md` 变了。检查命令必须仍是
`python3 check_marker.py`。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/main.md`，然后是 `logs/lessons.md`，然后是
  `SNAPSHOT`。`state.txt` 不是 prompt 文件，也不是 ledger。
- `restart_delay_s = 60`。`max_rounds = 3`。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
