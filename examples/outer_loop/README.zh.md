> 中文 · **[English](README.md)**

# 外环 — 2026 harness 思路落到 serve 上

`agent-runner serve` 是 **在线宿主**：一轮一个编码 CLI，加上隔离、pre-OOM、
give-up。分层和约束见 [examples/README.zh.md](../README.zh.md)。

复制本目录。**不要**自己循环 `agent-runner round`。**不要**在 78/75/70 上
自动重启。

JSONL 是只追加的历史（[events.md](../../docs/events.md)）。`peek --json` 是
当前 TOML，不是「哪份配置产出了过去那一轮」。

## 对照

| 思路 | 对方在改什么 | 怎么落地 |
|---|---|---|
| [Dream-RSI](https://www.dream-rsi.com/) O1–O3（Zheng et al., 2026, [arXiv:2609.14858](https://arxiv.org/abs/2609.14858)） | 冻结的编码 agent；历史当作*精确* replay；只改策略代码 | `serve` + CLI；`history_index.py` 倒出已发生的 **round**（线性，不是分叉树）；策略用 [between_rounds](../between_rounds/README.zh.md) 换；回执是 `config_digest`（[digest_watch](../digest_watch/README.zh.md)） |
| [Karpathy autoresearch](https://github.com/karpathy/autoresearch) | 一个文件、一个指标、固定预算、留下或丢掉 | 一份 prompt；`[[goal.checks]]`；`round_budget_s`；`[vcs] dirty_action`。人改 prompt（他们的 `program.md`）；agent 改仓库 |
| [RSIAgent](https://arxiv.org/abs/2609.15364) 课程 / 演员 / 阅卷 | 课程出题；演员执行；阅卷落地环境 | 课程 = 外层 queue。演员 = round child。阅卷 = `[[goal.checks]]` — **不是** core 里的 LLM 法官 |
| [RSI-Exam](https://rsi-exam.ai/) 隐藏集 | 可见集上迭代；隐藏数据上只打一次分 | 隐藏阅卷**不要**进 `[prompt] files`。外层拿着考卷 |
| [Ralph / harness engineering](https://openai.com/index/harness-engineering/) | 同一提示直到机械检查通过；仓库是系统真源 | 一直跑 `serve`；`AGENTS.md` / prompt 当热文件；linter 当 `[[goal.checks]]`；品味写进测试，不写进 core |
| [ACE](https://arxiv.org/abs/2510.04618)（ICLR 2026） | 条目化 playbook；Generator / Reflector / Curator；**增量**更新，禁止整份重写 | Generator = child。Reflector+Curator = 外层。Playbook = 额外 `[prompt] files`（不要用 `[goal] ledger` — 会截到 8192）。追加子弹，不要重写整份 prompt |
| [Codex 内环](https://openai.com/index/unrolling-the-codex-agent-loop/) | 一次 thread 里 compact、cache 前缀、tool 循环 | 留给 Codex。外环墙钟是 `round_budget_s`。不要给 serve 加 compact |
| [OpenHands workspace](https://docs.openhands.dev/sdk) | 每个 agent 一块隔离工作区 | `[agent] exec_prefix` / [container-pi](../../docs/recipes/container-pi.md)。supervisor 自己的隔离是 systemd + cgroup，不是再做一个 SDK |
| [Letta / MemGPT](https://github.com/letta-ai/letta-code) 记忆 OS | Core / archival / recall；agent 改自己的记忆 | 不进 core（杀路保持盲目）。耐久笔记 = 额外 prompt 文件。ledger 是 treadmill 上限，不是内存 |
| Subagent 扇出（Codex workers 等） | 并行子代理，各自上下文 | **一轮一进程。** 并行 = 多个 `serve` 单元，或内环 CLI。不要教 serve 去 spawn worker |

## 不用改 core 就能跑的配方

**Ralph。** serve 开着。同一份 prompt 直到 `[[goal.checks]]` 变绿。一直红就把
更紧的 prompt 丢进 between_rounds — 仍然一个 serve。

**ACE playbook。** `prompts/playbook.md` 写进 `[prompt] files`。`goal_check`
失败后外层**追加**一条子弹（增量，不是整份重写）。确认下一轮
`config_digest` 动了。Generator（child）只**读** playbook。

**Dream 一圈。** `history_index.py --log-dir logs` → 你的策略改写器 →
between_rounds `--queue`。看到 give-up 种类就停，不要自动重启。

**Autoresearch。** 仓库里一个可改文件、一条 check、想结束谱系时用
`max_rounds` 或 `stop_file`。

冷键（日程、host-health、`[agent] command`、check **条数**）仍要
`agent-runner restart`。热键（prompt 字节、check **命令行**）不用。见
[configuration.md](../../docs/configuration.md) § Config reload。

## 这个仓库不会变成

- 内环 tool loop、compact 或 prompt cache
- replay 引擎 / discovery-tree 存储 / 策略开发 agent
- `serve` 里的 explore / exam **模式**
- LLM 法官或查询引擎（[thesis.md](../../docs/thesis.md)）
- 记忆 OS 或自动 playbook 策展器

`history_index.py` 只**读**完整的 JSONL 行。
