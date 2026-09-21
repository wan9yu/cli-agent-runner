> 中文 · **[English](README.md)**

# 轮间换文件

把 `between_rounds.py` 拷到已经在跑 `agent-runner serve` 的项目旁边。它不是
第二个 supervisor。

日程、隔离、pre-OOM、give-up 仍由 serve 负责。这个进程只在轮与轮之间替换
**热**文件。语义见 [configuration.md](../../docs/configuration.md) § Config reload。

## 什么时候用

想改下一轮的 prompt 或 check 命令行，又不想重启 serve。冷键和混合键
（`round_budget_s`、check **条数**、`[agent]` command、日程、host-health）
仍要 `agent-runner restart`。

**不要**自己循环 `agent-runner round`。**不要**在 78/75/70 上自动重启 serve。

## 用法

```bash
python examples/between_rounds/between_rounds.py \
  --log-dir logs \
  --prompt prompts/main.md \
  --queue /tmp/next-prompt.md
```

每次 `round_end` 之后，若 `--queue` 存在且能通过 prompt 冒烟（非空、≥ 500
字节、首字符不是 `-` / 空白），就用 tmp + fsync + `os.replace` 写到
`--prompt`，然后删掉 queue。**下一轮** child 会重读。

必须熬过 treadmill 的额外笔记，放到另一个 `[prompt] files` 条目里，不要放进
`[goal] ledger`（child 会往里写 advisory，并截断到 8192 字节）。

## Give-up

事件流出现 `config_broken`、`crash_loop`、`stalled_no_progress` 或
`mem_loop_persistent` 时，脚本打印种类并退出，**不**重启 serve。修好配置或
主机后，自己 `agent-runner start` / `restart`。

## 历史 vs 当前配置

`peek --json` 会立刻重载 TOML，可能和 serve 的开机副本不一致。轮间归因看
`log_dir/events-YYYY-MM.jsonl` 上的 `round_start.config_digest`
（[events.md](../../docs/events.md)）：哈希的是列出的 prompt **路径和字节**，
所以同路径改正文也会变。`config_changed=true` 不能证明是你刚换上的文件 —
`[prompt] files` 里的 `[goal]` ledger 也会推动 digest（treadmill advisory，
或 agent 自己改了它）。
