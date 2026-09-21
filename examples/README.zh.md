> 中文 · **[English](README.md)**

# 示例 — 对着正在跑的 serve 做外环

这些**不是**第二个 supervisor。复制某个目录到已经在跑
`agent-runner serve` 的项目旁边。日程、隔离、pre-OOM、give-up 仍由 serve
负责。语义见 [docs/configuration.md](../docs/configuration.md) § Config reload。

**不要**自己循环调用 `agent-runner round`。**不要**在 78/75/70 上自动重启
serve。

## 我们怎么想

2026 年所谓 “harness” 文章，多半是 **外环**：不改编码 CLI，只改下一轮读到
什么。`serve` 是中间的进程层。这些示例不是第二个 supervisor。

| 层 | 做什么 | 不做什么 |
|---|---|---|
| 内环 | 一次 CLI 会话里 tool → observe → compact | 重写 Codex / pi / OpenHands |
| 外环（`serve`） | 一轮一进程；超时、cgroup、give-up、JSONL | 课程表、replay、考试模式 |
| 再外一层（`examples/`） | 改热文件、读历史 | 接管调度、隔离或 give-up |

寿命划分是诚实的（冷 / 热 / 混合）。替换是原子的。事件可读。杀进程路径
不读 `config_digest` 或 goal 笔记。

- **between_rounds** — 换下一轮 prompt；不接管调度、隔离或 give-up。78/75/70
  保持停住，直到人来 restart。
- **digest_watch** — 打印 child 哈希了什么（路径和字节）。不是 give-up 输入。
- **outer_loop** — 把 Dream-RSI / ACE / Ralph / Codex 映射到上面两个脚本。
  这些循环留在 `serve` 外面。

| 目录 | 做什么 |
|---|---|
| [between_rounds/](between_rounds/README.zh.md) | `round_end` 之后原子替换 prompt |
| [digest_watch/](digest_watch/README.zh.md) | 打印 `config_digest` / `config_changed` |
| [outer_loop/](outer_loop/README.zh.md) | 2026 harness 对照（Dream-RSI、ACE、Ralph、Codex 内环 vs 我们的外环） |

各 CLI 的安装步骤仍在 [docs/recipes/](../docs/recipes/)。

## 不用新进程的用法

**日夜 prompt。** 傍晚窗口前把 `night.md` 写到 between_rounds 的 `--queue`；
早上写 `day.md`。冷键（`round_budget_s`、`[agent] command`、日程）仍要
`agent-runner restart`。

**检查失败 → 下一句指令。** `[[goal.checks]]` 变红时，往同一个 queue 丢一份
「先修这个检查」的 prompt。课表留在 serve 外面。
