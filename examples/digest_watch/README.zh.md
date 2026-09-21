> 中文 · **[English](README.md)**

# Digest 灯

打印正在跑的 serve 上每一次 `round_start.config_digest`。同路径改正文会推动
digest；改 host-health 不会。它不是 supervisor，也不会重启 serve。

```bash
python examples/digest_watch/digest_watch.py --log-dir logs
```

示例一行：`R4 5eda8cf12c6c changed=True`。

`config_changed=true` 不能证明是*你的*那次替换落地了 — `[prompt] files` 里的
`[goal]` ledger 也会推动哈希。换文件用
[between_rounds](../between_rounds/README.zh.md)；JSONL 约定见
[events.md](../../docs/events.md)。
