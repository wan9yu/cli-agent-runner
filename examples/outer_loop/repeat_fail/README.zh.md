> 中文 · **[English](README.md)**

# 重复失败 — 同一种红才停

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `notes.md` 和 `prompts/` 拷进一个新的 git 仓库。不要把
   `fail_source.py` 或 `delay.py` 拷进那个仓库。
2. 把 `fail_source.py` 放在仓库旁边。它打印 `0.25`，退出码 1。它不读
   仓库。会翻掉这个值的赋值是 `VALUE = 1.0`。这一行不在 prompt 里，也不在
   真正的失败源里。
3. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。把 `WORK`、`PI`、
   `FAIL_SOURCE` 和 `STOP` 换成绝对路径。`FAIL_SOURCE` 和 `STOP` 都在仓库
   旁边。`STOP` 这时必须还不存在。
4. 把 `delay.py` 拷到仓库旁边。在这份配方目录里运行
   `python3 -m py_compile delay.py`，再运行 `python3 delay.py --self-check`。
   它会打印 `SELF_CHECK_OK`、`first_red_recorded=1` 和
   `would_stop_after_second_identical_red=1`。它不创建 `STOP`。在 Linux
   上还会打印 `proc_create_time_match=1`。
5. 先启动 delay 脚本，再启动一次 serve：

```bash
REPEAT_WORK=<仓库> REPEAT_CFG=<旁边目录> REPEAT_STOP=<STOP> python3 delay.py
agent-runner --config <agent-runner.toml 的路径> serve
```

`prompts/program.md` 只让孩子改 `notes.md`。它不要求把检查修绿，也不含
`VALUE = 1.0`。

## delay 脚本写什么

`delay.py` 是唯一的外层写入者。它不启动 serve，不循环 `agent-runner round`，
也不在 78、75 或 70 上再起。它不读助手文本。advisory 不是停。

它读 JSON `logs/serve.pid`。裸整数，或没有 `create_time` 的记录，都不授权
写入。在 Linux 上，它对照 `/proc` 启动时间，相差要小于 1 秒。它不用
`ps -o lstart=`。

第 1 轮红：记下 `satisfied` 和 `value`。不创建 `STOP`。第 2 轮仍红：指纹
没变，就在 60 秒 delay 内、第 3 轮 `round_start` 之前创建 `STOP`。

有有限 `value` 时，指纹是 `(satisfied, value)`。两轮都没有 `value` 时，
指纹只比 `satisfied`，两轮都红仍然匹配。一轮有 `value`、一轮没有，不匹配。
两轮 `value` 不同，也不匹配。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl` 和 delay 日志。退出码 0
只表示 serve 停了。

- 两轮红的 `goal_check` 指纹相同。没有 `round_num` 的检查，归到包围它的
  `round_start` 和 `round_end`。
- 第 1 轮 delay 行是 `decision=record`，并且 `stop_created=0`。
- 第 2 轮 delay 行先有 `fingerprints_equal=1`，再有
  `decision=create stop_created=1 in_delay=1 before_round3_start=1`。
- 终止事件是 `stop_file_detected`，不是 `max_rounds_reached`。没有第 3 轮
  `round_start`。

value 不同、停文件写晚了，或终止事件不是 `stop_file_detected`，就不是这次
落地。不要加轮，也不要再起一次 serve。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/program.md`，然后是 `logs/lessons.md`。
  `fail_source.py` 不是 prompt 路径。
- `restart_delay_s = 60`。`max_rounds = 3`。`round_budget_s = 1800`
  是每轮墙钟，所以停下来不是因为轮数用尽。
- `dirty_action = "ignore"`。`stash` 不是停。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
- `stop_file` 在启动时配好。文件本身要到轮间才创建。
