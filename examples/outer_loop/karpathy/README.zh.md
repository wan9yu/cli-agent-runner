> 中文 · **[English](README.md)**

# Karpathy — 一个文件，留下或丢掉

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `train.py` 和 `prompts/` 拷进一个新的 git 仓库。不要把 `snapshot/`
   或 `queue/` 拷进那个仓库。
2. 把 `snapshot/train.py` 放在仓库旁边。这是启动快照，serve 之前必须和
   仓库里的 `train.py` 相同。把 `queue/round2.md` 也放在仓库旁边。它不是
   prompt 路径。
3. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。把 `WORK` 换成
   仓库路径，`PI` 换成 pi 二进制。不要设置 `stop_file`。
4. 把 `delay.py` 拷到仓库旁边。运行 `python3 -m py_compile delay.py`，再
   运行 `python3 delay.py --self-check`。它会打印 `SELF_CHECK_OK`。
5. 先启动 delay 脚本，再启动一次 serve：

```bash
KARPATHY_WORK=<仓库> KARPATHY_SNAPSHOT=<snapshot/train.py> \
  KARPATHY_QUEUE=<queue/round2.md> python3 delay.py
agent-runner --config <agent-runner.toml 的路径> serve
```

`prompts/program.md` 是第 1 轮任务。它只点名一处更差修改 `FLOOR = 1`，
不写更好的修改。`queue/round2.md` 要求把指标提高，并且不再写 `FLOOR = 1`。
不要把更好的赋值贴进这两份文件。

## delay 脚本写什么

`delay.py` 从 `logs/events-YYYY-MM.jsonl` 读 `goal_check.value`。它不读
助手文本。它只在 `logs/serve.pid` 里的 pid 仍活着、还在 60 秒 delay 内、
并且下一轮还没开始时写入。文件里只有数字，不授权写入。

启动指标是 `10.0`。数值更大更好。缺失浮点不是留下。

第 1 轮之后，若 value 是有限浮点数且小于 `10.0`，这是丢掉。它用
`os.replace` 把启动快照写回 `train.py`，再在队列文件仍通过 prompt smoke
时，用 `os.replace` 把 `prompts/program.md` 换成那份队列文件一次。smoke
失败则两处都不写。它不跑 `git checkout`，也不跑 `stash`。

第 2 轮之后，若 value 是有限浮点数且大于 `10.0`，这是留下。它把 `train.py`
拷进快照。不恢复源文件，也不再换 prompt。第 3 轮不写快照，也不换 prompt。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl` 和快照旁边的 delay 日志。

- 一个文件：`train.py`。一个指标：`goal_check` 的 name 是 `metric`。
- 固定预算：`max_rounds_reached`，`max_rounds` 是 3。`agent_spawn` 的
  `timeout_s` 1800 是每轮墙钟，不是这个预算。
- 丢掉行含 `decision=discard`，恢复哈希等于种子哈希，两份 prompt 哈希不同。
- 留下行含 `decision=keep`，快照哈希与种子不同，并且 `not_restore=1`。
- 检查命令仍是 `python3 train.py`。

少了一行就是这个结果。不要加轮，也不要再起一次 serve。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/program.md`，然后是 `logs/lessons.md`。
  队列文件不是 prompt 路径。
- `restart_delay_s = 60`。`max_rounds = 3`。`round_budget_s = 1800`
  是挂起墙钟。
- `dirty_action = "ignore"`。`stash` 不是丢掉。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
- 这份配方不创建 `stop_file`。
