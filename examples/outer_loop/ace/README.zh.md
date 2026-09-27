> 中文 · **[English](README.md)**

# ACE — 追加一条 playbook 子弹

这一页是一份配方。[对照表](../README.zh.md)不是任务清单。

## 步骤

1. 把 `generator.py` 和 `prompts/` 拷进一个新的 git 仓库。不要把
   `playbook.md` 拷进那个仓库。
2. 把 `playbook.md` 放在仓库旁边。它是种好的 playbook，不是 ledger。
   里面只有一条固定条目，没有 delta 子弹。
3. 把 `agent-runner.toml.example` 拷成仓库旁边的 `agent-runner.toml`。
   `--config` 打开的是 `agent-runner.toml`，不是 example。把 `WORK`、
   `PI`、`PLAYBOOK` 换成仓库路径、pi 二进制、旁边的 playbook。不要设置
   `stop_file`。
4. 把 `delay.py` 拷到仓库旁边。运行 `python3 -m py_compile delay.py`，再
   运行 `python3 delay.py --self-check`。它会打印 `SELF_CHECK_OK`、
   `WRITE_ONCE round1=1 round2=0` 和 `BULLET_FROM_FIELD`。
5. 先启动 delay 脚本，再启动一次 serve：

```bash
ACE_WORK=<仓库> ACE_PLAYBOOK=<playbook.md> python3 delay.py
agent-runner --config <agent-runner.toml 的路径> serve
```

`prompts/program.md` 只让孩子改 `generator.py`。不要把 playbook 路径写进
任何一份交给孩子的文件。不要把 `ACE_DELTA` 写进种子文件。

## delay 脚本写什么

`delay.py` 在第 1 轮 `round_end` 之后、第 2 轮 `round_start` 之前追加一条
子弹。子弹只拷贝 `goal_check` 的 `name`、`value`、`satisfied`。它不读助手
文本，也不调用另一个模型。value 不是有限浮点数时不追加。它用临时文件、
fsync 和 `os.replace`。种子字节仍是前缀。这不是整份替换。第 2 轮不再追加。

它只在 `logs/serve.pid` 里的 pid 仍活着、并且还在 60 秒 delay 内时写入。
文件里只有数字，不授权写入。它不写 ledger，也不换 `files[0]`。

## 停下来看什么

serve 退出后，打开 `logs/events-YYYY-MM.jsonl`、delay 日志和
`logs/rounds/R1-*.log`。

- 追加之前，playbook 哈希等于种子哈希。固定条目仍是前缀。
- delay 日志的 `decision=write` 早于第 2 轮 `round_start`。前缀哈希仍等于
  种子。后缀是一条新子弹 `- ACE_DELTA name=... value=... satisfied=...`，
  来自这一轮 `goal_check` 的字段。
- 第 1 轮没有 `ACE_DELTA`。第 2 轮仍以固定条目开头，不是整份换成另一条。
- 第 2 轮不再追加第二条。
- 检查命令仍是 `python3 generator.py`。playbook 路径不是 `logs/lessons.md`。

如果文件被整份替换，或子弹来自孩子文本，就不是这份配方。不要加轮，也不要
再起一次 serve。

## 例子里已经写好的设置

- `[prompt] files` 是 `prompts/program.md`，然后是 `logs/lessons.md`，然后是
  `PLAYBOOK`。playbook 不是 `[goal] ledger`。ledger 仍会截到 8192。这份文件
  不会。
- `restart_delay_s = 60`。`max_rounds = 2`。`round_budget_s = 1800`
  是每轮墙钟。
- `dirty_action = "ignore"`。
- 孩子 argv 是 `pi -p -na --mode json --model PROVIDER/MODEL`。
  不要加 `--no-session`。
- 这份配方不创建 `stop_file`。
