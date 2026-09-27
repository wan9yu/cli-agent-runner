> 中文 · **[English](README.md)**

# 花费上限 — 把事件加总，然后停

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `marker.md` 拷成新 git 仓库里的 `README.md`。把 `prompts/` 拷进
   那个仓库。不要把 `delay.py` 或这一页拷进仓库。
2. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。把 `WORK`、
   `PI` 和 `STOP` 换成绝对路径。相对的 `WORK` 会按配置文件所在目录解析。
   相对的 `STOP` 会落在仓库里面，delay 脚本不会在那里创建它。`STOP`
   在仓库旁边，这时必须还不存在。把 `PROVIDER/MODEL` 换成会发出
   `cost_usd` 的模型。孩子 argv 是 pi 二进制，然后
   `-p -na --mode json --model PROVIDER/MODEL`。不要加 `--tools`。
   不要加 `--no-session`。不要加预算表。
3. 把 `delay.py` 拷到仓库旁边。在这份配方目录里运行
   `python3 -m py_compile delay.py`，再运行 `python3 delay.py --self-check`。
   它会打印 `SELF_CHECK_OK`、`missing_usage_advanced=0` 和
   `ceiling_stop_temp_only=1`。它不创建 `STOP`。它不打印钱或 token 的
   总数。这份标准输出不是 serve 的轮间行。在 Linux 上还会打印
   `proc_create_time_match=1`。
4. 先启动 delay 脚本，再启动一次 serve。不要循环 `agent-runner round`。
   不要在 78、75 或 70 上再起。不要加轮。不要再起一次 serve。

```bash
SPEND_WORK=<仓库> SPEND_CFG=<旁边目录> SPEND_STOP=<STOP> python3 delay.py
agent-runner --config <agent-runner.toml 的路径> serve
```

`prompts/program.md` 要求一句包含 `MARKER` 的话。它不提 `cost_usd`。

## delay 脚本写什么

`delay.py` 是唯一的外层写入者。它不启动 serve。它不改冷命令。它不读
goal 笔记。它不写钱或 token 的总数。

它读 JSON `logs/serve.pid`。裸整数，或没有 `create_time` 的记录，都不授权
写入。在 Linux 上，它对照 `/proc` 启动时间，相差要小于 1 秒。它不用
`ps -o lstart=`。

一轮 `round_end` 之后，它把已经结束的轮上 `agent_usage_recorded` 里正的
有限 `cost_usd` 加进列表。没有 usage 事件，不进入这个列表，也不当成零。
null 和零也不进入。上限是脚本里的一个正阈值。一个正的有限 `cost_usd`
就越过它。脚本不把这个阈值或加总写出来。

越过上限时，它在 60 秒 delay 内、下一轮 `round_start` 之前创建 `STOP`。
文件正文是词 `ceiling`。`max_rounds` 和 `round_budget_s` 不是这个加总。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl` 和 delay 日志。退出码 0
只表示 serve 停了。不要从这些文件里抄出钱或 token 的总数。

- delay 行有 `decision=write`、`summed=1`、`missing_usage_advanced=0`、
  `missing_not_zero=1`、`ceiling_crossed=1`、`stop_created=1`、
  `in_delay=1`、`before_next_round_start=1` 和 `total_recorded=0`。
  `total_recorded=0` 表示没有记下总数，不是把缺事件当成零。
  `cost_usd_events_in_sum` 是进入加总的事件计数，不是加总本身。
- `STOP` 存在。正文没有数字，也没有 `$`。
- 终止事件是 `stop_file_detected`。没有第 2 轮，也没有
  `max_rounds_reached`。
- `max_rounds` 仍是 3。`round_budget_s` 仍是 1800。这两个都不是加总。
  没有 `[budget]` 表。

usage 事件缺了，上限就不越过。不要把缺事件当成零。不要编一个正数。
不要加轮，也不要再起一次 serve。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/program.md`，然后是 `logs/lessons.md`。
- `restart_delay_s = 60`。`max_rounds = 3`。`round_budget_s = 1800`
  是每轮墙钟，所以停下来不是因为轮数用尽。
- `dirty_action = "ignore"`。`stash` 不是上限。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--tools`。不要加 `--no-session`。
- `stop_file` 在启动时配好。文件本身要到轮间才创建。
